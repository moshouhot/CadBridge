// CadBridge shared plugin source — execution-context baseline for the pause-state read.
//
// WHY THIS EXISTS
// ---------------
// LiveReadLispFunction reads the drawing from inside a callback invoked while AutoLISP is
// paused at a breakpoint. A successful DocumentLock + transaction proves the read was legal,
// but NOT that the callback ran on the expected thread or context. The safety review asked
// for an independently established baseline rather than a comment asserting legality.
//
// This type records that baseline. The intended use is:
//
//   1. With AutoCAD idle at the command line (no LISP running, no debugger attached), run
//      CBBRIDGEBASELINE. It records the native thread id and context facts. When a readiness
//      path is configured, the in-memory baseline and readiness file publish transactionally.
//   2. The pause-state read then compares its own thread/context against that baseline and
//      REFUSES BEFORE TOUCHING THE DOCUMENT if they disagree.
//
// The refusal happens before any document or database API call, because a check performed
// afterwards cannot prevent the access it was meant to guard.
//
// STATUS: NOT YET EXERCISED. No live run has been performed since this was added (live
// harnesses are gated pending the safety review). The baseline is therefore EMPTY, and the
// read path will refuse rather than proceed — which is the intended fail-closed behaviour,
// but it means the live path is currently unusable by design. The Autodesk-documented
// guarantees for this callback context have also NOT been verified yet.
using System;
using System.Globalization;
using System.IO;
using System.Text;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Runtime;

using Application = Autodesk.AutoCAD.ApplicationServices.Core.Application;
using SysException = System.Exception;

namespace CadBridge.Plugin.Shared
{
    /// <summary>
    /// Records the idle command-context thread so the pause-state read can verify it is running
    /// in the expected context before it touches the document.
    /// </summary>
    public static class ExecutionContextBaseline
    {
        /// <summary>Native (OS) thread id observed at an idle command context, or 0 if unset.</summary>
        private static uint _idleNativeThreadId;

        /// <summary>Managed thread id observed at an idle command context, or 0 if unset.</summary>
        private static int _idleManagedThreadId;

        /// <summary>Official AutoCAD execution-context flag captured at baseline.</summary>
        private static bool _idleIsApplicationContext;

        /// <summary>Whether the official context flag was successfully captured.</summary>
        private static bool _hasContextBaseline;

        /// <summary>Serializes the one-time baseline transition.</summary>
        private static readonly object BaselineLock = new object();

        private static bool HasBaselineUnsafe()
        {
            return _idleNativeThreadId != 0 && _hasContextBaseline;
        }

        /// <summary>Whether a baseline has been recorded in this session.</summary>
        public static bool HasBaseline
        {
            get
            {
                lock (BaselineLock)
                {
                    return HasBaselineUnsafe();
                }
            }
        }

        public static uint IdleNativeThreadId
        {
            get
            {
                lock (BaselineLock)
                {
                    return _idleNativeThreadId;
                }
            }
        }

        public static int IdleManagedThreadId
        {
            get
            {
                lock (BaselineLock)
                {
                    return _idleManagedThreadId;
                }
            }
        }

        /// <summary>
        /// Records the current thread as the idle command-context baseline.
        ///
        /// This setter is deliberately NOT exported as a LispFunction. A paused debugger can
        /// evaluate user-defined Lisp functions; allowing (CBBASELINE) there would let the
        /// paused context overwrite the trusted idle reference and defeat the later check.
        /// The only external setter is CBBRIDGEBASELINE, invoked by the startup script before
        /// DAP attaches.
        /// </summary>
        private static void ClearBaselineUnsafe()
        {
            _idleNativeThreadId = 0;
            _idleManagedThreadId = 0;
            _idleIsApplicationContext = false;
            _hasContextBaseline = false;
        }

        /// <summary>
        /// Establishes the in-memory candidate while the caller holds <see cref="BaselineLock"/>.
        /// The caller is responsible for publishing the readiness signal before releasing the
        /// lock. If readiness publication fails, it rolls this attempt back under the same lock
        /// so Check() can never observe the transient candidate.
        /// </summary>
        private static string RecordBaselineUnsafe(out bool establishedThisAttempt)
        {
            establishedThisAttempt = false;

            // A trusted baseline is a session invariant, not mutable state. Once an idle
            // command context has established it, no later command/debugger path may
            // replace it. Failed attempts leave the baseline unset so startup may retry.
            if (HasBaselineUnsafe())
            {
                return "CBBASELINE_REFUSED status=already_recorded";
            }

            uint native = GetCurrentThreadId();
            int managed = System.Threading.Thread.CurrentThread.ManagedThreadId;

            bool appContext;
            try { appContext = Application.DocumentManager.IsApplicationContext; }
            catch (SysException ex)
            {
                ClearBaselineUnsafe();
                return "CBBASELINE_REFUSED status=context_query_failed error_type="
                     + ex.GetType().Name;
            }

            if (appContext)
            {
                ClearBaselineUnsafe();
                return "CBBASELINE_REFUSED status=application_context";
            }

            _idleNativeThreadId = native;
            _idleManagedThreadId = managed;
            _idleIsApplicationContext = appContext;
            _hasContextBaseline = true;
            establishedThisAttempt = true;

            var sb = new StringBuilder();
            sb.Append("CBBASELINE_RECORDED");
            sb.Append(" native_thread_id=").Append(native.ToString(CultureInfo.InvariantCulture));
            sb.Append(" managed_thread_id=").Append(managed.ToString(CultureInfo.InvariantCulture));
            sb.Append(" is_application_context=").Append(appContext ? "true" : "false");
            sb.Append(" has_document=").Append(
                Application.DocumentManager.MdiActiveDocument != null ? "true" : "false");
            sb.Append(" src=CadBridge.Plugin.Shared.ExecutionContextBaseline");
            return sb.ToString();
        }

        private static void PublishReadinessUnsafe(string path, string result)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return;
            }

            string dir = Path.GetDirectoryName(path);
            if (!string.IsNullOrWhiteSpace(dir))
            {
                Directory.CreateDirectory(dir);
            }
            File.WriteAllText(path, result + Environment.NewLine, new UTF8Encoding(false));
        }

        /// <summary>
        /// Records the baseline from a real AutoCAD command context BEFORE DAP attaches.
        /// The live harness sets CB_BASELINE_LOG_PATH and runs this command from its startup
        /// script immediately after NETLOAD.  Writing the result to a dedicated file gives the
        /// harness a deterministic readiness signal without COM, SendCommand, or debugger
        /// evaluation, and prevents the debugger from accidentally overwriting the baseline.
        /// </summary>
        [CommandMethod("CBBRIDGEBASELINE")]
        public static void RecordBaselineCommand()
        {
            string path = Environment.GetEnvironmentVariable("CB_BASELINE_LOG_PATH");

            // The in-memory baseline and its readiness file form one publication transaction.
            // Check() uses this same lock, so it cannot observe the provisional state below.
            // If readiness I/O fails after the candidate was established, roll it back BEFORE
            // releasing the lock. A later startup attempt can then retry without restarting CAD.
            lock (BaselineLock)
            {
                bool establishedThisAttempt;
                string result = RecordBaselineUnsafe(out establishedThisAttempt);
                try
                {
                    PublishReadinessUnsafe(path, result);
                }
                catch
                {
                    if (establishedThisAttempt)
                    {
                        ClearBaselineUnsafe();
                    }
                    throw;
                }
            }
        }

        /// <summary>
        /// Compares the CURRENT thread against the recorded baseline.
        /// Returns (matches, reason). A missing baseline is a NON-match, so the caller fails
        /// closed rather than proceeding on an unverified assumption.
        /// </summary>
        public static bool Check(out string reason)
        {
            uint native = GetCurrentThreadId();
            int managed = System.Threading.Thread.CurrentThread.ManagedThreadId;

            bool appContext;
            try { appContext = Application.DocumentManager.IsApplicationContext; }
            catch (SysException ex)
            {
                reason = "could not query AutoCAD execution context: " + ex.GetType().Name;
                return false;
            }
            if (appContext)
            {
                reason = "AutoCAD reports application execution context; read probe requires document context";
                return false;
            }
            uint idleNative;
            int idleManaged;
            bool idleAppContext;
            bool hasBaseline;
            lock (BaselineLock)
            {
                // Read the four baseline fields as one published snapshot.  The setter uses
                // this same lock, so Check can never observe a half-published baseline.
                hasBaseline = HasBaselineUnsafe();
                idleNative = _idleNativeThreadId;
                idleManaged = _idleManagedThreadId;
                idleAppContext = _idleIsApplicationContext;
            }

            if (!hasBaseline)
            {
                reason = "no baseline recorded (run CBBRIDGEBASELINE while AutoCAD is idle); "
                       + "refusing because thread identity cannot be verified";
                return false;
            }
            if (appContext != idleAppContext)
            {
                reason = "AutoCAD execution context differs from idle baseline";
                return false;
            }
            if (native != idleNative)
            {
                reason = "native thread id " + native.ToString(CultureInfo.InvariantCulture)
                       + " != baseline " + idleNative.ToString(CultureInfo.InvariantCulture);
                return false;
            }
            if (managed != idleManaged)
            {
                reason = "managed thread id " + managed.ToString(CultureInfo.InvariantCulture)
                       + " != baseline " + idleManaged.ToString(CultureInfo.InvariantCulture);
                return false;
            }
            reason = "AutoCAD document context and thread identity match the idle baseline";
            return true;
        }

        [System.Runtime.InteropServices.DllImport("kernel32.dll")]
        private static extern uint GetCurrentThreadId();
    }
}
