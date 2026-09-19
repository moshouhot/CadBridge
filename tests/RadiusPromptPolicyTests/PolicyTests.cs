// CadBridge host-independent tests for RadiusPromptPolicy.
//
// SCOPE (do not overstate): these tests exercise PURE decision logic against the REAL
// Autodesk PromptStatus enum. No AutoCAD process, document, editor, prompt or geometry is
// involved. They prove that the mapping refuses cancellation and unknown statuses; they do
// NOT prove live interactive cancellation inside a running AutoCAD.
using System;
using CadBridge.Plugin.Shared;
using Autodesk.AutoCAD.EditorInput;

// NOTE: this namespace must NOT end in `RadiusPromptPolicy`, or it shadows the
// production type of the same name and every `RadiusPromptPolicy.Decide` call
// resolves to the namespace instead of the class.
namespace CadBridge.Tests.PromptPolicy
{
    internal static class Program
    {
        private static int _passed;
        private static int _failed;

        private static void Check(string name, bool ok, string detail)
        {
            if (ok)
            {
                _passed++;
                Console.WriteLine("  PASS  " + name);
            }
            else
            {
                _failed++;
                Console.WriteLine("  FAIL  " + name + " :: " + detail);
            }
        }

        private static int Main()
        {
            Console.WriteLine("== RadiusPromptPolicy: real enum values ==");
            // Pin the actual SDK values so a future reference-set change is visible rather
            // than silently changing the meaning of the policy.
            Check("PromptStatus.OK == 5100", (int)PromptStatus.OK == 5100, ((int)PromptStatus.OK).ToString());
            Check("PromptStatus.None == 5000", (int)PromptStatus.None == 5000, ((int)PromptStatus.None).ToString());
            Check("PromptStatus.Cancel == -5002", (int)PromptStatus.Cancel == -5002, ((int)PromptStatus.Cancel).ToString());
            Check("PromptStatus.Error == -5001", (int)PromptStatus.Error == -5001, ((int)PromptStatus.Error).ToString());
            Check("PromptStatus.Keyword == -5005", (int)PromptStatus.Keyword == -5005, ((int)PromptStatus.Keyword).ToString());

            Console.WriteLine("== RadiusPromptPolicy: decisions ==");

            double r;
            string why;

            // OK -> use the typed value.
            var d = RadiusPromptPolicy.Decide(PromptStatus.OK, 42.5, out r, out why);
            Check("OK uses the supplied value",
                  d == RadiusPromptPolicy.Decision.UseValue && r == 42.5, d + "/" + r);

            // None is the documented "Enter accepts the default" path: create, with default.
            d = RadiusPromptPolicy.Decide(PromptStatus.None, 0.0, out r, out why);
            Check("None creates with the default radius",
                  d == RadiusPromptPolicy.Decision.UseDefault && r == RadiusPromptPolicy.DefaultRadius,
                  d + "/" + r);

            // THE REGRESSION: cancellation must create nothing.
            d = RadiusPromptPolicy.Decide(PromptStatus.Cancel, 0.0, out r, out why);
            Check("Cancel is refused (the original defect)",
                  d == RadiusPromptPolicy.Decision.Reject, d.ToString());

            d = RadiusPromptPolicy.Decide(PromptStatus.Error, 0.0, out r, out why);
            Check("Error is refused", d == RadiusPromptPolicy.Decision.Reject, d.ToString());

            d = RadiusPromptPolicy.Decide(PromptStatus.Keyword, 0.0, out r, out why);
            Check("Keyword is refused", d == RadiusPromptPolicy.Decision.Reject, d.ToString());

            d = RadiusPromptPolicy.Decide(PromptStatus.Modeless, 0.0, out r, out why);
            Check("Modeless is refused", d == RadiusPromptPolicy.Decision.Reject, d.ToString());

            // Fail-closed on a status this SDK generation does not define.
            d = RadiusPromptPolicy.Decide((PromptStatus)12345, 0.0, out r, out why);
            Check("unknown status is refused (fail closed)",
                  d == RadiusPromptPolicy.Decision.Reject, d.ToString());

            // A refused decision must never hand back a usable radius by accident.
            d = RadiusPromptPolicy.Decide(PromptStatus.Cancel, 99.0, out r, out why);
            Check("a refused decision reports radius 0, not the stale value",
                  d == RadiusPromptPolicy.Decision.Reject && r == 0.0, r.ToString());

            // A non-positive value that the user really typed must still be rejected later by
            // CreateCircle; the policy only reports what was entered.
            d = RadiusPromptPolicy.Decide(PromptStatus.OK, -5.0, out r, out why);
            Check("a typed negative value is passed through for CreateCircle to reject",
                  d == RadiusPromptPolicy.Decision.UseValue && r == -5.0, d + "/" + r);

            Console.WriteLine();
            Console.WriteLine("radius-prompt-policy tests: " + _passed + " passed, " + _failed + " failed");
            return _failed == 0 ? 0 : 1;
        }
    }
}
