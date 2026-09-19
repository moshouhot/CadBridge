// CadBridge shared plugin source — interactive radius prompt policy.
//
// WHY THIS TYPE EXISTS
// --------------------
// DiagnosticsCommands.CircleWithArgs() reads one optional radius from the command line and
// then creates a circle. The original version was:
//
//     double radius = 10.0;
//     var res = ed.GetDouble(opts);
//     if (res.Status == PromptStatus.OK) { radius = res.Value; }
//     CreateCircle(100.0, 100.0, radius);      // <-- runs for EVERY status
//
// That is a real defect: it does not distinguish "the user accepted the default" from "the
// user pressed Escape" or "the prompt failed". Cancelling the command, or a prompt error,
// still created a circle with the default radius. A rejected command must not leave
// geometry behind.
//
// The decision is extracted here as a PURE function over the REAL Autodesk PromptStatus enum
// so it can be verified without a live AutoCAD session. No mirrored numeric constants are
// used: a hand-copied status table could silently drift from the SDK, which is exactly the
// kind of unverifiable claim this project forbids.
using Autodesk.AutoCAD.EditorInput;

namespace CadBridge.Plugin.Shared
{
    /// <summary>
    /// Pure, AutoCAD-free decision logic for the interactive radius prompt.
    /// Kept free of document/database access so a test can exercise it directly.
    /// </summary>
    public static class RadiusPromptPolicy
    {
        /// <summary>The radius used when the caller accepts the prompt default (presses Enter).</summary>
        public const double DefaultRadius = 10.0;

        /// <summary>What the caller should do with a prompt result.</summary>
        public enum Decision
        {
            /// <summary>Use the value the user typed.</summary>
            UseValue,

            /// <summary>No value was entered; use <see cref="DefaultRadius"/>.</summary>
            UseDefault,

            /// <summary>Create nothing. The command was cancelled or the prompt failed.</summary>
            Reject,
        }

        /// <summary>
        /// Maps a numeric prompt result to a decision.
        ///
        /// Only <see cref="PromptStatus.OK"/> and <see cref="PromptStatus.None"/> may create
        /// geometry. <see cref="PromptStatus.None"/> is the documented "Enter accepts the
        /// default" path and is therefore intentional, not an error. Every other status —
        /// Cancel, Error, Keyword, Modeless, and any value this SDK generation does not
        /// define — is a refusal, so an unrecognised status fails closed rather than
        /// silently creating a default-radius circle.
        /// </summary>
        public static Decision Decide(PromptStatus status, double value,
                                      out double radius, out string reason)
        {
            switch (status)
            {
                case PromptStatus.OK:
                    radius = value;
                    reason = "user supplied a value";
                    return Decision.UseValue;

                case PromptStatus.None:
                    radius = DefaultRadius;
                    reason = "no value entered; accepting the default radius";
                    return Decision.UseDefault;

                case PromptStatus.Cancel:
                    radius = 0.0;
                    reason = "prompt cancelled by the user";
                    return Decision.Reject;

                case PromptStatus.Error:
                    radius = 0.0;
                    reason = "prompt returned an error";
                    return Decision.Reject;

                case PromptStatus.Keyword:
                    radius = 0.0;
                    reason = "a keyword was entered where a numeric radius was required";
                    return Decision.Reject;

                default:
                    // Deliberately the fail-closed branch: an unrecognised status must not be
                    // treated as consent to create geometry.
                    radius = 0.0;
                    reason = "unsupported prompt status " + ((int)status).ToString(
                        System.Globalization.CultureInfo.InvariantCulture);
                    return Decision.Reject;
            }
        }
    }
}
