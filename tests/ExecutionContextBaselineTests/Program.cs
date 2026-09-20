using System;
using System.Collections.Concurrent;
using System.IO;
using System.Threading.Tasks;
using CadBridge.Plugin.Shared;
using Application = Autodesk.AutoCAD.ApplicationServices.Core.Application;

namespace CadBridge.Tests.ExecutionContextBaseline
{
    internal static class Program
    {
        private static int passed;
        private static int failed;

        private static void Check(string name, bool ok, object detail = null)
        {
            if (ok)
            {
                passed++;
                Console.WriteLine("  PASS  " + name);
            }
            else
            {
                failed++;
                Console.WriteLine("  FAIL  " + name + (detail == null ? "" : " :: " + detail));
            }
        }

        private static int Main()
        {
            Console.WriteLine("== ExecutionContextBaseline publication transaction ==");
            Application.DocumentManager.IsApplicationContext = false;
            Application.DocumentManager.MdiActiveDocument = new object();

            string root = Path.Combine(Path.GetTempPath(), "CadBridge-baseline-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                Check("baseline begins unset", !ExecutionContextBaseline.HasBaseline);

                // Deterministic readiness failure: the would-be parent directory is a file.
                string blocker = Path.Combine(root, "not-a-directory");
                File.WriteAllText(blocker, "block");
                string badPath = Path.Combine(blocker, "idle-baseline.txt");
                Environment.SetEnvironmentVariable("CB_BASELINE_LOG_PATH", badPath);

                bool publishFailed = false;
                try
                {
                    ExecutionContextBaseline.RecordBaselineCommand();
                }
                catch (IOException)
                {
                    publishFailed = true;
                }
                catch (UnauthorizedAccessException)
                {
                    publishFailed = true;
                }

                Check("readiness publication failure propagates", publishFailed);
                Check("failed publication leaves baseline unset", !ExecutionContextBaseline.HasBaseline);
                Check("failed publication clears native id", ExecutionContextBaseline.IdleNativeThreadId == 0,
                      ExecutionContextBaseline.IdleNativeThreadId);
                Check("failed publication clears managed id", ExecutionContextBaseline.IdleManagedThreadId == 0,
                      ExecutionContextBaseline.IdleManagedThreadId);

                // Retry with a writable path. This is the regression for the old lockout.
                string goodPath = Path.Combine(root, "good", "idle-baseline.txt");
                Environment.SetEnvironmentVariable("CB_BASELINE_LOG_PATH", goodPath);
                ExecutionContextBaseline.RecordBaselineCommand();

                string goodText = File.ReadAllText(goodPath);
                Check("retry writes readiness file", goodText.Contains("CBBASELINE_RECORDED"), goodText);
                Check("retry commits baseline", ExecutionContextBaseline.HasBaseline);
                uint native = ExecutionContextBaseline.IdleNativeThreadId;
                int managed = ExecutionContextBaseline.IdleManagedThreadId;
                Check("committed ids are nonzero", native != 0 && managed != 0, native + "/" + managed);

                string reason;
                Check("same-thread Check succeeds after commit",
                      ExecutionContextBaseline.Check(out reason), reason);

                // A successful baseline remains one-shot.
                string thirdPath = Path.Combine(root, "third", "idle-baseline.txt");
                Environment.SetEnvironmentVariable("CB_BASELINE_LOG_PATH", thirdPath);
                ExecutionContextBaseline.RecordBaselineCommand();
                string thirdText = File.ReadAllText(thirdPath);
                Check("third attempt is refused as already_recorded",
                      thirdText.Contains("CBBASELINE_REFUSED status=already_recorded"), thirdText);
                Check("third attempt does not replace ids",
                      ExecutionContextBaseline.IdleNativeThreadId == native
                      && ExecutionContextBaseline.IdleManagedThreadId == managed);

                // Readers race with repeated refused re-record attempts. Worker-thread Check()
                // should fail on thread identity, but it must never observe "no baseline".
                Environment.SetEnvironmentVariable("CB_BASELINE_LOG_PATH", null);
                var errors = new ConcurrentQueue<string>();
                Task[] readers = new Task[4];
                for (int i = 0; i < readers.Length; i++)
                {
                    readers[i] = Task.Run(() =>
                    {
                        for (int j = 0; j < 2000; j++)
                        {
                            string why;
                            ExecutionContextBaseline.Check(out why);
                            if (why.Contains("no baseline"))
                            {
                                errors.Enqueue(why);
                            }
                            if (!ExecutionContextBaseline.HasBaseline
                                || ExecutionContextBaseline.IdleNativeThreadId == 0
                                || ExecutionContextBaseline.IdleManagedThreadId == 0)
                            {
                                errors.Enqueue("reader observed an unpublished/torn baseline");
                            }
                        }
                    });
                }

                for (int i = 0; i < 2000; i++)
                {
                    ExecutionContextBaseline.RecordBaselineCommand();
                }
                Task.WaitAll(readers);
                Check("concurrent readers never observe an unpublished/torn baseline",
                      errors.IsEmpty, string.Join(" | ", errors.ToArray()));

                Console.WriteLine();
                Console.WriteLine("execution-baseline tests: " + passed + " passed, " + failed + " failed");
                return failed == 0 ? 0 : 1;
            }
            finally
            {
                Environment.SetEnvironmentVariable("CB_BASELINE_LOG_PATH", null);
                try { Directory.Delete(root, true); } catch { }
            }
        }
    }
}
