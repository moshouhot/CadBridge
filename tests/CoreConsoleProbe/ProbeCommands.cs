// CadBridge diagnostic: does the AutoCAD Core Engine Console (accoreconsole.exe) load a
// managed plugin at all, and is acmgd.dll the reason a full-UI plugin cannot load there?
//
// This project deliberately references ONLY accoremgd + acdbmgd (no acmgd), so it can run
// inside accoreconsole. It exists to isolate a load failure, not as product code.
//
// Findings it is designed to produce:
//   * CBHELLO registered  => accoreconsole DOES NETLOAD managed plugins, and the earlier
//                            failure of CadBridge.Plugin.Legacy was caused by its acmgd
//                            reference (the UI-level managed assembly is absent headless).
//   * CBHELLO not registered => accoreconsole does not accept this plugin shape at all.
using System;
using Autodesk.AutoCAD.Runtime;
// Autodesk.AutoCAD.ApplicationServices.Core lives in accoremgd.dll and is the
// core-console-safe facade. It is NOT the same type as ApplicationServices.Application,
// which lives in acmgd.dll and is unavailable in accoreconsole.
using CoreApp = Autodesk.AutoCAD.ApplicationServices.Core.Application;

namespace CadBridge.Diagnostics.CoreConsoleProbe
{
    public sealed class ProbeCommands
    {
        [CommandMethod("CBHELLO", CommandFlags.Modal)]
        public void Hello()
        {
            string msg = "CBHELLO OK"
                       + "; clr=" + Environment.Version
                       + "; arch=" + (Environment.Is64BitProcess ? "x64" : "x86")
                       + "; asm=" + GetType().Assembly.Location;

            try
            {
                var doc = CoreApp.DocumentManager.MdiActiveDocument;
                if (doc != null)
                {
                    msg += "; doc=" + doc.Name;
                    doc.Editor.WriteMessage("\n" + msg + "\n");
                    return;
                }
                msg += "; doc=<none>";
            }
            catch (System.Exception ex)
            {
                msg += "; editor_error=" + ex.GetType().Name;
            }

            // Fall back to stdout so the probe reports even without an editor.
            Console.WriteLine(msg);
        }
    }
}
