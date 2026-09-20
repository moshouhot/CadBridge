using System;
using System.Collections.Concurrent;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Baseline = global::CadBridge.Plugin.Shared.ExecutionContextBaseline;
using Application = Autodesk.AutoCAD.ApplicationServices.Core.Application;

namespace CadBridge.Tests.ExecutionBaselineRegression
{
    internal static class Program
    {
        private sealed class Snapshot
        {
            internal bool HasBaseline;
            internal uint Native;
            internal int Managed;
            internal string Reason;
        }

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

        private static Task[] StartReaders(
            ManualResetEventSlim candidateEntered,
            CountdownEvent readersReady,
            ManualResetEventSlim releaseCandidate,
            ConcurrentBag<Snapshot> snapshots,
            ref int completedBeforeRelease)
        {
            var readers = new Task[4];
            for (int i = 0; i < readers.Length; i++)
            {
                readers[i] = Task.Run(() =>
                {
                    candidateEntered.Wait();
                    readersReady.Signal();

                    var snap = new Snapshot();
                    snap.HasBaseline = Baseline.HasBaseline;
                    snap.Native = Baseline.IdleNativeThreadId;
                    snap.Managed = Baseline.IdleManagedThreadId;
                    string reason;
                    Baseline.Check(out reason);
                    snap.Reason = reason;
                    snapshots.Add(snap);

                    if (!releaseCandidate.IsSet)
                    {
                        Interlocked.Increment(ref completedBeforeRelease);
                    }
                });
            }
            return readers;
        }

        private static bool ReadersRemainBlockedUntilRelease(
            CountdownEvent readersReady,
            ref int completedBeforeRelease)
        {
            if (!readersReady.Wait(TimeSpan.FromSeconds(5)))
            {
                return false;
            }

            // The test-only writer hook is still holding the publication point. With the
            // production lock intact, no reader can finish a baseline getter/Check here.
            // Removing the transaction lock lets at least one reader observe provisional state.
            return !SpinWait.SpinUntil(
                () => Volatile.Read(ref completedBeforeRelease) != 0,
                TimeSpan.FromMilliseconds(750));
        }

        private static int Main()
        {
            Console.WriteLine("== ExecutionContextBaseline publication transaction ==");
            Application.DocumentManager.IsApplicationContext = false;
            Application.DocumentManager.MdiActiveDocument = new object();

            string root = Path.Combine(
                Path.GetTempPath(), "CadBridge-baseline-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);

            try
            {
                Check("baseline begins unset", !Baseline.HasBaseline);

                // -----------------------------------------------------------------
                // ROLLBACK RACE: readers start before the first publication and try
                // to read while the writer is paused on a provisional candidate.
                // -----------------------------------------------------------------
                string blocker = Path.Combine(root, "not-a-directory");
                File.WriteAllText(blocker, "block");
                string badPath = Path.Combine(blocker, "idle-baseline.txt");
                Environment.SetEnvironmentVariable("CB_BASELINE_LOG_PATH", badPath);

                using (var candidateEntered = new ManualResetEventSlim(false))
                using (var releaseCandidate = new ManualResetEventSlim(false))
                using (var readersReady = new CountdownEvent(4))
                {
                    var snapshots = new ConcurrentBag<Snapshot>();
                    int completedBeforeRelease = 0;

                    Baseline.TestAfterCandidateEstablished = () =>
                    {
                        candidateEntered.Set();
                        if (!readersReady.Wait(TimeSpan.FromSeconds(5)))
                        {
                            throw new TimeoutException("rollback readers did not reach publication window");
                        }
                        releaseCandidate.Wait();
                    };

                    Task[] readers = StartReaders(
                        candidateEntered, readersReady, releaseCandidate,
                        snapshots, ref completedBeforeRelease);

                    Exception writerError = null;
                    Task writer = Task.Run(() =>
                    {
                        try
                        {
                            Baseline.RecordBaselineCommand();
                        }
                        catch (Exception ex)
                        {
                            writerError = ex;
                        }
                    });

                    Check("rollback candidate window reached",
                          candidateEntered.Wait(TimeSpan.FromSeconds(5)));
                    Check("rollback readers are blocked before release",
                          ReadersRemainBlockedUntilRelease(readersReady, ref completedBeforeRelease),
                          "completed_before_release=" + completedBeforeRelease);

                    releaseCandidate.Set();
                    Task.WaitAll(readers);
                    writer.Wait();

                    Check("readiness publication failure propagates",
                          writerError is IOException || writerError is UnauthorizedAccessException,
                          writerError == null ? "no exception" : writerError.GetType().Name);
                    Check("failed publication leaves baseline unset", !Baseline.HasBaseline);
                    Check("failed publication clears native id", Baseline.IdleNativeThreadId == 0,
                          Baseline.IdleNativeThreadId);
                    Check("failed publication clears managed id", Baseline.IdleManagedThreadId == 0,
                          Baseline.IdleManagedThreadId);
                    Check("rollback readers see only fully-unset state",
                          snapshots.All(s => !s.HasBaseline && s.Native == 0 && s.Managed == 0
                                             && s.Reason.Contains("no baseline")),
                          string.Join(" | ", snapshots.Select(
                              s => s.HasBaseline + "/" + s.Native + "/" + s.Managed + "/" + s.Reason)));
                }

                Baseline.TestAfterCandidateEstablished = null;

                // -----------------------------------------------------------------
                // COMMIT RACE: repeat the controlled first-publication window, but
                // this time readiness succeeds. Readers must remain blocked until
                // the committed state is externally publishable.
                // -----------------------------------------------------------------
                string goodPath = Path.Combine(root, "good", "idle-baseline.txt");
                Environment.SetEnvironmentVariable("CB_BASELINE_LOG_PATH", goodPath);

                bool writerCheckOk = false;
                string writerCheckReason = "";
                using (var candidateEntered = new ManualResetEventSlim(false))
                using (var releaseCandidate = new ManualResetEventSlim(false))
                using (var readersReady = new CountdownEvent(4))
                {
                    var snapshots = new ConcurrentBag<Snapshot>();
                    int completedBeforeRelease = 0;

                    Baseline.TestAfterCandidateEstablished = () =>
                    {
                        candidateEntered.Set();
                        if (!readersReady.Wait(TimeSpan.FromSeconds(5)))
                        {
                            throw new TimeoutException("commit readers did not reach publication window");
                        }
                        releaseCandidate.Wait();
                    };

                    Task[] readers = StartReaders(
                        candidateEntered, readersReady, releaseCandidate,
                        snapshots, ref completedBeforeRelease);

                    Exception writerError = null;
                    Task writer = Task.Run(() =>
                    {
                        try
                        {
                            Baseline.RecordBaselineCommand();
                            writerCheckOk = Baseline.Check(out writerCheckReason);
                        }
                        catch (Exception ex)
                        {
                            writerError = ex;
                        }
                    });

                    Check("commit candidate window reached",
                          candidateEntered.Wait(TimeSpan.FromSeconds(5)));
                    Check("commit readers are blocked before release",
                          ReadersRemainBlockedUntilRelease(readersReady, ref completedBeforeRelease),
                          "completed_before_release=" + completedBeforeRelease);

                    releaseCandidate.Set();
                    Task.WaitAll(readers);
                    writer.Wait();

                    Check("successful publication has no writer exception",
                          writerError == null, writerError);
                    Check("readiness file written", File.Exists(goodPath));
                    string goodText = File.ReadAllText(goodPath);
                    Check("readiness file records commit",
                          goodText.Contains("CBBASELINE_RECORDED"), goodText);
                    Check("successful publication commits baseline", Baseline.HasBaseline);
                    Check("committed ids are nonzero",
                          Baseline.IdleNativeThreadId != 0 && Baseline.IdleManagedThreadId != 0,
                          Baseline.IdleNativeThreadId + "/" + Baseline.IdleManagedThreadId);
                    Check("writer-thread Check succeeds after commit",
                          writerCheckOk, writerCheckReason);
                    Check("commit readers see only fully-published state",
                          snapshots.All(s => s.HasBaseline && s.Native != 0 && s.Managed != 0
                                             && !s.Reason.Contains("no baseline")),
                          string.Join(" | ", snapshots.Select(
                              s => s.HasBaseline + "/" + s.Native + "/" + s.Managed + "/" + s.Reason)));
                }

                Baseline.TestAfterCandidateEstablished = null;

                uint native = Baseline.IdleNativeThreadId;
                int managed = Baseline.IdleManagedThreadId;

                // A successful baseline remains one-shot.
                string thirdPath = Path.Combine(root, "third", "idle-baseline.txt");
                Environment.SetEnvironmentVariable("CB_BASELINE_LOG_PATH", thirdPath);
                Baseline.RecordBaselineCommand();
                string thirdText = File.ReadAllText(thirdPath);
                Check("third attempt is refused as already_recorded",
                      thirdText.Contains("CBBASELINE_REFUSED status=already_recorded"), thirdText);
                Check("third attempt does not replace ids",
                      Baseline.IdleNativeThreadId == native
                      && Baseline.IdleManagedThreadId == managed);

                Console.WriteLine();
                Console.WriteLine(
                    "execution-baseline tests: " + passed + " passed, " + failed + " failed");
                return failed == 0 ? 0 : 1;
            }
            finally
            {
                Baseline.TestAfterCandidateEstablished = null;
                Environment.SetEnvironmentVariable("CB_BASELINE_LOG_PATH", null);
                try { Directory.Delete(root, true); } catch { }
            }
        }
    }
}
