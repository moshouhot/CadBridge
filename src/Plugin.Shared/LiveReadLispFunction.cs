// CadBridge shared plugin source — P1/T01-5: Plugin-mediated pause-state read probe.
//
// WHY THIS EXISTS
// ---------------
// T01-5 requires reading the drawing through the PLUGIN while AutoLISP is stopped at a
// breakpoint. Measurement showed the DAP channel behaves differently for plugin code than
// for plain Lisp:
//
//   * contexts "hover"/"watch"/"clipboard" REFUSE user-defined functions outright
//     (the adapter answers: 无法在监视窗口中计算用户定义的函数 -- "cannot evaluate a
//     user-defined function in the watch window"), so built-ins such as (entget (entlast))
//     work there but plugin code cannot;
//   * context "repl" DOES invoke user-defined functions, and the value comes back on stdout
//     rather than in the response `result` field.
//
// This type is the plugin-side probe for the repl path. It is a probe, not a product
// surface, and it is deliberately read-only.
//
// INSTRUMENTATION (added after review)
// -----------------------------------
// A successful lock+transaction is NOT by itself evidence that the callback ran in the
// expected context. The review asked for the callback to measure its own circumstances, so
// the return value now reports, as parseable key=value fields:
//
//   nonce        the caller-supplied nonce, so a result can be tied to one request
//   thread_id    the native (OS) thread the callback ran on
//   managed_tid  the managed thread id
//   in_command   whether AutoCAD reports an active command context
//   doc          the active document name, to detect a wrong-document read
//   depth        nesting depth, to detect reentrancy
//   lock_ms / tx_ms / read_ms   timings, so a stall is visible rather than inferred
//   entities / circles / last_circle_handle / last_circle_center / last_circle_radius
//                structured geometry (no free-text radius, so "r=3" cannot be confused
//                with "r=30")
//   disposed     whether the transaction and lock were released before returning
//
// It still does not, and cannot, prove the absence of an internal resume/re-stop inside the
// debugger. That must be established from the DAP side.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Threading;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Runtime;

// Core application facade (accoremgd), matching the rest of the shared source.
using Application = Autodesk.AutoCAD.ApplicationServices.Core.Application;

// Autodesk.AutoCAD.Runtime also defines an `Exception` type; alias System.Exception so the
// catch clause is unambiguous.
using SysException = System.Exception;

namespace CadBridge.Plugin.Shared
{
    public sealed class LiveReadLispFunction
    {
        /// <summary>Identifies the reader as plugin code rather than a plain-Lisp expression.</summary>
        private const string Marker = "src=CadBridge.Plugin.Shared.LiveReadLispFunction";

        /// <summary>Nesting depth, so reentrancy is observable rather than assumed.</summary>
        private static int _depth;

        /// <summary>
        /// Reads model-space circles and returns a one-line, parseable summary.
        /// Lisp name: (CBLIVEREAD [nonce])
        ///
        /// The optional nonce lets a caller correlate one result with one request. Passing it
        /// is strongly preferred; without it the result cannot be tied to a specific call.
        /// </summary>
        [LispFunction("CBLIVEREAD")]
        public object LiveRead(ResultBuffer args)
        {
            var sw = Stopwatch.StartNew();
            string nonce = ExtractNonce(args);

            var sb = new StringBuilder();
            sb.Append("CBLIVEREAD");
            sb.Append(" nonce=").Append(nonce);
            sb.Append(" thread_id=").Append(GetNativeThreadId().ToString(CultureInfo.InvariantCulture));
            sb.Append(" managed_tid=").Append(Thread.CurrentThread.ManagedThreadId.ToString(CultureInfo.InvariantCulture));

            int depth = Interlocked.Increment(ref _depth);
            sb.Append(" depth=").Append(depth.ToString(CultureInfo.InvariantCulture));

            // REENTRANCY IS REFUSED BEFORE ANY DOCUMENT OR DATABASE ACCESS.
            //
            // Merely reporting the depth afterwards would be insufficient: by then the
            // document APIs would already have been touched, which is the situation the check
            // exists to prevent. A nested entry returns immediately without acquiring a lock,
            // opening a transaction, or reading anything.
            if (depth > 1)
            {
                Interlocked.Decrement(ref _depth);
                sb.Append(" status=refused_reentrant");
                sb.Append(" disposed=n/a");
                return sb.Append(' ').Append(Marker).ToString();
            }

            // THREAD/CONTEXT IDENTITY IS VERIFIED BEFORE ANY DOCUMENT OR DATABASE ACCESS.
            //
            // A check performed after the read could not prevent the access it guards. If no
            // idle baseline has been recorded (run CBBASELINE first) or the current thread does
            // not match it, this refuses immediately and touches nothing.
            try
            {
                string reason;
                if (!ExecutionContextBaseline.Check(out reason))
                {
                    Interlocked.Decrement(ref _depth);
                    sb.Append(" status=refused_context_unverified");
                    sb.Append(" context_reason=").Append(Sanitize(reason));
                    sb.Append(" disposed=n/a");
                    return sb.Append(' ').Append(Marker).ToString();
                }
                sb.Append(" context=verified");
            }
            catch (SysException ex)
            {
                Interlocked.Decrement(ref _depth);
                sb.Append(" status=refused_context_check_failed");
                sb.Append(" error_type=").Append(Sanitize(ex.GetType().Name));
                sb.Append(" disposed=n/a");
                return sb.Append(' ').Append(Marker).ToString();
            }

            try
            {
                bool appContext;
                try { appContext = Application.DocumentManager.IsApplicationContext; }
                catch (SysException ex)
                {
                    sb.Append(" status=refused_context_query_failed");
                    sb.Append(" error_type=").Append(Sanitize(ex.GetType().Name));
                    return sb.Append(' ').Append(Marker).ToString();
                }
                sb.Append(" context_app=").Append(appContext ? "true" : "false");
                sb.Append(" in_command=").Append(appContext ? "false" : "true");

                var doc = Application.DocumentManager.MdiActiveDocument;
                if (doc == null)
                {
                    sb.Append(" status=no_active_document");
                    return sb.Append(' ').Append(Marker).ToString();
                }

                sb.Append(" doc=").Append(Sanitize(doc.Name));

                Transaction tr = null;
                bool disposed = false;
                try
                {
                    // Autodesk documents read/query access as not requiring an explicit
                    // document lock; AutoLISP functions receive basic document locking from
                    // AutoCAD. Avoid adding a lock inside a debugger-paused read path.
                    sb.Append(" lock_mode=not_requested_read_only");
                    long txStart = sw.ElapsedMilliseconds;
                    tr = doc.Database.TransactionManager.StartTransaction();
                    long txDone = sw.ElapsedMilliseconds;
                    sb.Append(" tx_ms=").Append((txDone - txStart).ToString(CultureInfo.InvariantCulture));

                    var bt = (BlockTable)tr.GetObject(doc.Database.BlockTableId, OpenMode.ForRead);
                    var ms = (BlockTableRecord)tr.GetObject(
                        bt[BlockTableRecord.ModelSpace], OpenMode.ForRead);

                    int circleCount = 0;
                    int entityCount = 0;
                    string lastHandle = "none";
                    string lastCenter = "none";
                    string lastRadius = "none";
                    foreach (ObjectId id in ms)
                    {
                        entityCount++;
                        var c = tr.GetObject(id, OpenMode.ForRead) as Circle;
                        if (c == null)
                        {
                            continue;
                        }
                        circleCount++;
                        lastHandle = c.Handle.ToString();
                        lastCenter = string.Format(CultureInfo.InvariantCulture, "{0},{1},{2}",
                            c.Center.X, c.Center.Y, c.Center.Z);
                        lastRadius = c.Radius.ToString("R", CultureInfo.InvariantCulture);
                    }
                    long readDone = sw.ElapsedMilliseconds;

                    sb.Append(" read_ms=").Append((readDone - txDone).ToString(CultureInfo.InvariantCulture));
                    sb.Append(" entities=").Append(entityCount.ToString(CultureInfo.InvariantCulture));
                    sb.Append(" circles=").Append(circleCount.ToString(CultureInfo.InvariantCulture));
                    sb.Append(" last_circle_handle=").Append(lastHandle);
                    sb.Append(" last_circle_center=").Append(lastCenter);
                    sb.Append(" last_circle_radius=").Append(lastRadius);
                    sb.Append(" status=ok");
                }
                catch (SysException ex)
                {
                    sb.Append(" status=error");
                    sb.Append(" error_type=").Append(Sanitize(ex.GetType().Name));
                    sb.Append(" error=").Append(Sanitize(ex.Message));
                }
                finally
                {
                    // Read-only by design: the transaction is DISPOSED WITHOUT Commit, so it
                    // aborts.
                    //
                    // Each disposal is reported TRUTHFULLY. An earlier version set
                    // disposed=true unconditionally, which would have reported success even
                    // when disposal threw -- exactly the kind of optimistic claim this
                    // project forbids. `disposed` is true only if BOTH disposals completed
                    // without error, and any failure is surfaced in the return value.
                    string trDisposed = "n/a";
                    if (tr != null)
                    {
                        try { tr.Dispose(); trDisposed = "ok"; }
                        catch (SysException ex)
                        {
                            trDisposed = "error:" + Sanitize(ex.GetType().Name);
                        }
                    }
                    disposed = trDisposed == "ok";
                    sb.Append(" tr_disposed=").Append(trDisposed);
                    sb.Append(" lock_disposed=n/a_read_only");
                }
                sb.Append(" disposed=").Append(disposed ? "true" : "false");
                sb.Append(" total_ms=").Append(sw.ElapsedMilliseconds.ToString(CultureInfo.InvariantCulture));
                return sb.Append(' ').Append(Marker).ToString();
            }
            catch (SysException ex)
            {
                // Report the failure as data rather than throwing, so the caller sees a real
                // diagnosis instead of an opaque Lisp error.
                return sb.Append(" status=error error_type=").Append(Sanitize(ex.GetType().Name))
                         .Append(" error=").Append(Sanitize(ex.Message))
                         .Append(' ').Append(Marker).ToString();
            }
            finally
            {
                Interlocked.Decrement(ref _depth);
            }
        }

        /// <summary>
        /// Reads the optional nonce argument. Accepts a string or any Lisp atom and
        /// stringifies it; a missing argument yields "none" so absence is visible.
        /// </summary>
        private static string ExtractNonce(ResultBuffer args)
        {
            if (args == null)
            {
                return "none";
            }
            try
            {
                // ResultBuffer does not implement IEnumerable<TypedValue> on every
                // supported generation; AsArray() is the portable accessor.
                TypedValue[] list = args.AsArray();
                if (list == null || list.Length == 0)
                {
                    return "none";
                }
                object v = list[0].Value;
                return v == null ? "none" : Sanitize(Convert.ToString(v, CultureInfo.InvariantCulture));
            }
            catch (SysException)
            {
                return "unreadable";
            }
        }

        /// <summary>Keeps the single-line key=value contract intact.</summary>
        private static string Sanitize(string s)
        {
            if (string.IsNullOrEmpty(s))
            {
                return "none";
            }
            var sb = new StringBuilder(s.Length);
            foreach (char ch in s)
            {
                sb.Append(char.IsWhiteSpace(ch) ? '_' : ch);
            }
            return sb.ToString();
        }

        /// <summary>
        /// Native thread id, obtained from the current process/thread handles so the value is
        /// the OS thread and can be compared with what the host reports.
        /// </summary>
        private static uint GetNativeThreadId()
        {
            try
            {
                return GetCurrentThreadId();
            }
            catch (SysException)
            {
                return 0;
            }
        }

        [System.Runtime.InteropServices.DllImport("kernel32.dll")]
        private static extern uint GetCurrentThreadId();
    }
}
