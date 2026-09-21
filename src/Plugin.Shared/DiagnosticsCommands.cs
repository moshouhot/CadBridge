// CadBridge shared plugin source — P1/T01-1 CAD-context PoC commands.
//
// Scope is deliberately narrow. This is NOT CadBridge's protocol, registry, batching,
// revision tracking or pipe transport; those are P2/P3 work and must not be guessed into
// a PoC.
//
// What these commands exist to prove, on a real AutoCAD:
//   1. A CommandMethod executes on AutoCAD's main thread in a real document context.
//   2. DocumentLock + Transaction can be opened, committed and disposed correctly.
//   3. The reported runtime is measured, not a build constant.
//   4. A write is persisted to disk and can be verified by a SEPARATE process that reopens
//      the saved drawing and reads the geometry back.
//
// (4) matters because re-running our own in-process query is not an independent oracle:
// it shares the same process, the same loaded assembly and the same in-memory state as the
// write. Only reopening the saved file in a fresh process rules out an in-memory illusion.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;

// IMPORTANT: use the CORE application facade (accoremgd.dll), not
// Autodesk.AutoCAD.ApplicationServices.Application (acmgd.dll).
//
// OBSERVED (2026-09-17): accoreconsole.exe does not ship acmgd.dll, and a plugin that
// referenced acmgd failed to register its command there, while the same plugin compiled
// against Core.Application loaded and ran. That is consistent with acmgd being the cause,
// but it is NOT a controlled experiment: at the time of the first failure the assembly was
// accidentally built empty, so the original attribution is unproven.
//
// The choice of Core.Application is therefore made on the safe side rather than on a proven
// mechanism: it works in both full AutoCAD and the core engine, and it keeps one shared
// source tree loadable in either host. Genuinely UI-bound capabilities (view.capture, zoom,
// palettes) still require acmgd and must stay behind capability checks; out of P1 scope.
using Application = Autodesk.AutoCAD.ApplicationServices.Core.Application;

// Autodesk.AutoCAD.Runtime also defines an `Exception` type; alias System.Exception so every
// catch clause is unambiguous in shared source.
using SysException = System.Exception;

namespace CadBridge.Plugin.Shared
{
    public sealed class DiagnosticsCommands
    {
        /// <summary>
        /// Reports measured runtime, host identity, and proves the lock/transaction contract.
        /// Read-only. Command: CBBRIDGEINFO
        /// </summary>
        [CommandMethod("CBBRIDGEINFO", CommandFlags.Modal)]
        public void Info()
        {
            var doc = Application.DocumentManager.MdiActiveDocument;
            if (doc == null)
            {
                WriteLine("CBBRIDGEINFO ERROR: no active document");
                return;
            }

            var sb = new StringBuilder();
            sb.AppendLine("CBBRIDGEINFO BEGIN");
            sb.AppendLine("  " + RuntimeInfo.Summary());

            string acadVersion;
            try { acadVersion = Application.Version.ToString(); }
            catch (SysException ex) { acadVersion = "(error: " + ex.GetType().Name + ")"; }
            sb.AppendLine("  acad_application_version: " + acadVersion);

            string productVersion;
            try
            {
                string exe = System.Diagnostics.Process.GetCurrentProcess().MainModule?.FileName;
                productVersion = string.IsNullOrEmpty(exe)
                    ? "(unavailable)"
                    : (System.Diagnostics.FileVersionInfo.GetVersionInfo(exe).ProductVersion ?? "(null)");
            }
            catch (SysException ex) { productVersion = "(error: " + ex.GetType().Name + ")"; }
            sb.AppendLine("  acad_product_version: " + productVersion);
            sb.AppendLine("  document_name: " + doc.Name);

            try
            {
                using (doc.LockDocument())
                using (var tr = doc.Database.TransactionManager.StartTransaction())
                {
                    var bt = (BlockTable)tr.GetObject(doc.Database.BlockTableId, OpenMode.ForRead);
                    var ms = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForRead);
                    int count = 0;
                    foreach (ObjectId _ in ms) { count++; }
                    sb.AppendLine("  lock_and_transaction: OK");
                    sb.AppendLine("  model_space_entities: " + count);
                    sb.AppendLine("  drawing_insunits: " + doc.Database.Insunits);
                    // Read-only: no Commit. Disposing aborts cleanly.
                }
            }
            catch (SysException ex)
            {
                sb.AppendLine("  lock_and_transaction: FAILED " + ex.GetType().Name + ": " + ex.Message);
            }

            sb.AppendLine("CBBRIDGEINFO END");
            WriteLine(sb.ToString());
        }

        /// <summary>
        /// Creates exactly one circle in model space under an explicit transaction.
        /// Command: CBBRIDGEPINGCIRCLE [radius]
        /// Default centre (100,100,0), radius 10 — matching the F-CAD C1 fixture entry.
        /// </summary>
        [CommandMethod("CBBRIDGEPINGCIRCLE", CommandFlags.Modal)]
        public void PingCircle()
        {
            CreateCircle(100.0, 100.0, 10.0);
        }

        /// <summary>Create a circle with an explicit radius, for negative-input tests.</summary>
        [CommandMethod("CBBRIDGECIRCLE", CommandFlags.Modal)]
        public void CircleWithArgs()
        {
            // Read one optional numeric argument from the command line. Used by the negative
            // tests (radius 0 / negative must be rejected without leaving a partial entity).
            //
            // Cancelling the prompt must create NOTHING. The original version defaulted to
            // radius 10 for every non-OK status, so pressing Escape still drew a circle. The
            // decision now lives in RadiusPromptPolicy (pure, testable) and every status other
            // than OK/None is a refusal.
            var ed = Application.DocumentManager.MdiActiveDocument?.Editor;
            double radius = RadiusPromptPolicy.DefaultRadius;
            if (ed != null)
            {
                var opts = new Autodesk.AutoCAD.EditorInput.PromptDoubleOptions("\nRadius")
                {
                    AllowNone = true,
                    DefaultValue = RadiusPromptPolicy.DefaultRadius,
                };
                var res = ed.GetDouble(opts);

                double decided;
                string reason;
                var decision = RadiusPromptPolicy.Decide(res.Status, res.Value, out decided, out reason);
                if (decision == RadiusPromptPolicy.Decision.Reject)
                {
                    WriteLine("CBBRIDGECIRCLE CANCELLED status=" + res.Status + " reason=" + reason);
                    return;
                }
                radius = decided;
            }
            CreateCircle(100.0, 100.0, radius);
        }

        private static void CreateCircle(double cx, double cy, double radius)
        {
            var doc = Application.DocumentManager.MdiActiveDocument;
            if (doc == null)
            {
                WriteLine("CBBRIDGECIRCLE ERROR: no active document");
                return;
            }

            // Negative test: a non-positive radius must be refused with no partial effect.
            if (double.IsNaN(radius) || double.IsInfinity(radius) || radius <= 0.0)
            {
                WriteLine("CBBRIDGECIRCLE REJECTED invalid radius=" + radius.ToString("R", CultureInfo.InvariantCulture));
                return;
            }

            try
            {
                using (doc.LockDocument())
                using (var tr = doc.Database.TransactionManager.StartTransaction())
                {
                    var bt = (BlockTable)tr.GetObject(doc.Database.BlockTableId, OpenMode.ForRead);
                    var ms = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite);

                    var circle = new Circle(new Point3d(cx, cy, 0.0), Vector3d.ZAxis, radius);
                    circle.Layer = "0";
                    ObjectId id = ms.AppendEntity(circle);
                    tr.AddNewlyCreatedDBObject(circle, true);
                    tr.Commit();

                    WriteLine(string.Format(CultureInfo.InvariantCulture,
                        "CBBRIDGECIRCLE OK handle={0} layer=0 center={1},{2},0 r={3}",
                        id.Handle, cx, cy, radius));
                }
            }
            catch (SysException ex)
            {
                WriteLine("CBBRIDGECIRCLE FAILED " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        /// <summary>
        /// Reads back every CIRCLE in model space with real geometry.
        /// Command: CBBRIDGELISTCIRCLES
        ///
        /// When run in a freshly launched process against a saved drawing, this is an
        /// independent read-back: different process, no shared in-memory state.
        /// </summary>
        [CommandMethod("CBBRIDGELISTCIRCLES", CommandFlags.Modal)]
        public void ListCircles()
        {
            var doc = Application.DocumentManager.MdiActiveDocument;
            if (doc == null)
            {
                WriteLine("CBBRIDGELISTCIRCLES ERROR: no active document");
                return;
            }

            var sb = new StringBuilder();
            sb.AppendLine("CBBRIDGELISTCIRCLES BEGIN");
            sb.AppendLine("  drawing: " + doc.Name);
            int n = 0;
            try
            {
                using (doc.LockDocument())
                using (var tr = doc.Database.TransactionManager.StartTransaction())
                {
                    var bt = (BlockTable)tr.GetObject(doc.Database.BlockTableId, OpenMode.ForRead);
                    var ms = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForRead);
                    foreach (ObjectId id in ms)
                    {
                        var ent = tr.GetObject(id, OpenMode.ForRead) as Circle;
                        if (ent == null) { continue; }
                        n++;
                        sb.AppendLine(string.Format(CultureInfo.InvariantCulture,
                            "  CIRCLE handle={0} layer={1} center={2},{3},{4} r={5} normal={6},{7},{8}",
                            ent.Handle, ent.Layer,
                            ent.Center.X, ent.Center.Y, ent.Center.Z,
                            ent.Radius,
                            ent.Normal.X, ent.Normal.Y, ent.Normal.Z));
                    }
                }
                sb.AppendLine("  circle_count: " + n);
            }
            catch (SysException ex)
            {
                sb.AppendLine("  FAILED " + ex.GetType().Name + ": " + ex.Message);
            }
            sb.AppendLine("CBBRIDGELISTCIRCLES END");
            WriteLine(sb.ToString());
        }

        /// <summary>
        /// Saves the active drawing to an explicit path.
        /// Command: CBBRIDGESAVEAS (path read from the CB_SAVE_PATH environment variable)
        ///
        /// The path comes from an environment variable rather than a command argument so the
        /// test harness never has to inject quoting into a command script.
        /// Only ever used against disposable fixtures.
        /// </summary>
        [CommandMethod("CBBRIDGESAVEAS", CommandFlags.Modal)]
        public void SaveAs()
        {
            var doc = Application.DocumentManager.MdiActiveDocument;
            if (doc == null)
            {
                WriteLine("CBBRIDGESAVEAS ERROR: no active document");
                return;
            }

            string path = Environment.GetEnvironmentVariable("CB_SAVE_PATH");
            if (string.IsNullOrWhiteSpace(path))
            {
                WriteLine("CBBRIDGESAVEAS ERROR: CB_SAVE_PATH not set");
                return;
            }

            try
            {
                var dir = Path.GetDirectoryName(path);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                {
                    Directory.CreateDirectory(dir);
                }
                // SaveAs overwrites only the explicit test path.
                doc.Database.SaveAs(path, true, DwgVersion.Current, doc.Database.SecurityParameters);
                WriteLine("CBBRIDGESAVEAS OK path=" + path);
            }
            catch (SysException ex)
            {
                WriteLine("CBBRIDGESAVEAS FAILED " + ex.GetType().Name + ": " + ex.Message);
            }
        }

        /// <summary>
        /// Reports the layers present, proving we can read structural (non-geometric) data.
        /// Command: CBBRIDGELISTLAYERS
        /// </summary>
        [CommandMethod("CBBRIDGELISTLAYERS", CommandFlags.Modal)]
        public void ListLayers()
        {
            var doc = Application.DocumentManager.MdiActiveDocument;
            if (doc == null)
            {
                WriteLine("CBBRIDGELISTLAYERS ERROR: no active document");
                return;
            }
            var sb = new StringBuilder();
            sb.AppendLine("CBBRIDGELISTLAYERS BEGIN");
            try
            {
                using (doc.LockDocument())
                using (var tr = doc.Database.TransactionManager.StartTransaction())
                {
                    var lt = (LayerTable)tr.GetObject(doc.Database.LayerTableId, OpenMode.ForRead);
                    var names = new List<string>();
                    foreach (ObjectId id in lt)
                    {
                        var ltr = (LayerTableRecord)tr.GetObject(id, OpenMode.ForRead);
                        names.Add(ltr.Name);
                    }
                    names.Sort(StringComparer.Ordinal);
                    sb.AppendLine("  layers: " + string.Join(",", names.ToArray()));
                    sb.AppendLine("  layer_count: " + names.Count);
                }
            }
            catch (SysException ex)
            {
                sb.AppendLine("  FAILED " + ex.GetType().Name + ": " + ex.Message);
            }
            sb.AppendLine("CBBRIDGELISTLAYERS END");
            WriteLine(sb.ToString());
        }

        private static void WriteLine(string message)
        {
            // Channel 1: the AutoCAD editor. Only meaningful in a full GUI session, and it is
            // NOT captured when acad.exe runs with /b (a GUI process writes nothing to stdout).
            try
            {
                Application.DocumentManager.MdiActiveDocument?.Editor.WriteMessage("\n" + message + "\n");
            }
            catch
            {
                // Never let diagnostics reporting throw from a command.
            }

            // Channel 2: an explicit evidence file, enabled by the CB_LOG_PATH environment
            // variable. This exists because the GUI host gives no stdout, and because evidence
            // must be capturable without depending on a screen scrape. Never writes to a user
            // path unless the caller sets the variable.
            try
            {
                string path = Environment.GetEnvironmentVariable("CB_LOG_PATH");
                if (!string.IsNullOrWhiteSpace(path))
                {
                    var dir = Path.GetDirectoryName(path);
                    if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                    {
                        Directory.CreateDirectory(dir);
                    }
                    // Append with an explicit flush so a later hard exit cannot lose the record.
                    using (var fs = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite))
                    using (var sw = new StreamWriter(fs, new UTF8Encoding(false)))
                    {
                        sw.WriteLine(message);
                        sw.Flush();
                        fs.Flush();
                    }
                }
            }
            catch
            {
                // Logging must never change the outcome of a command.
            }
        }
    }
}
