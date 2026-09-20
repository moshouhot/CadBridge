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

        private sealed class Counter
        {
            internal int Value;
        }

        private static readonly TimeSpan SignalTimeout = TimeSpan.FromSeconds(5);
        private static readonly TimeSpan JoinTimeout = TimeSpan.FromSeconds(10);

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
            Counter completedBeforeRelease)
        {
            var readers = new Task[4];
            for (int i = 0; i < readers.Length; i++)
            {
                readers[i] = Task.Run(() =>
                {
                    bool entered = candidateEntered.Wait(SignalTimeout);
                    readersReady.Signal();
                    if (!entered)
                    {
                        snapshots.Add(new Snapshot
                        {
                            HasBaseline = false,
                            Native = 0,
                            Managed = 0,
                            Reason = "candidate-enter timeout"
                        });
                        Interlocked.Increment(ref completedBeforeRelease.Value);
                        return;
                    }

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
                        Interlocked.Increment(ref completedBeforeRelease.Value);
                    }
                });
            }
            return readers;
        }

        private static bool ReadersRemainBlockedUntilRelease(
            CountdownEvent readersReady,
            Counter completedBeforeRelease)
        {
            if (!readersReady.Wait(SignalTimeout))
            {
                return false;
            }

            // The test-only writer hook is still holding the publication point. With the
            // production lock intact, no reader can finish a baseline getter/Check here.
            // Removing the transaction lock lets at least one reader observe provisional state.
            return !SpinWait.SpinUntil(
                () => Volatile.Read(ref completedBeforeRelease.Value) != 0,
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
                    var completedBeforeRelease = new Counter();

                    Baseline.TestAfterCandidateEstablished = () =>
                    {
                        candidateEntered.Set();
                        if (!readersReady.Wait(SignalTimeout))
                        {
                            throw new TimeoutException("rollback readers did not reach publication window");
                        }
                        if (!releaseCandidate.Wait(SignalTimeout))
                        {
                            throw new TimeoutException("rollback release signal was not received");
                        }
                    };

                    Task[] readers = StartReaders(
                        candidateEntered, readersReady, releaseCandidate,
                        snapshots, completedBeforeRelease);

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
                          candidateEntered.Wait(SignalTimeout));
                    Check("rollback readers are blocked before release",
                          ReadersRemainBlockedUntilRelease(readersReady, completedBeforeRelease),
                          "completed_before_release=" + completedBeforeRelease.Value);

                    releaseCandidate.Set();
                    bool rollbackReadersDone = Task.WaitAll(readers, JoinTimeout);
                    bool rollbackWriterDone = writer.Wait(JoinTimeout);
                    Check("rollback readers finish within bound", rollbackReadersDone);
                    Check("rollback writer finishes within bound", rollbackWriterDone);

                    if (rollbackReadersDone && rollbackWriterDone)
                    {
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
                    var completedBeforeRelease = new Counter();

                    Baseline.TestAfterCandidateEstablished = () =>
                    {
                        candidateEntered.Set();
                        if (!readersReady.Wait(SignalTimeout))
                        {
                            throw new TimeoutException("commit readers did not reach publication window");
                        }
                        if (!releaseCandidate.Wait(SignalTimeout))
                        {
                            throw new TimeoutException("commit release signal was not received");
                        }
                    };

                    Task[] readers = StartReaders(
                        candidateEntered, readersReady, releaseCandidate,
                        snapshots, completedBeforeRelease);

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
                          candidateEntered.Wait(SignalTimeout));
                    Check("commit readers are blocked before release",
                          ReadersRemainBlockedUntilRelease(readersReady, completedBeforeRelease),
                          "completed_before_release=" + completedBeforeRelease.Value);

                    releaseCandidate.Set();
                    bool commitReadersDone = Task.WaitAll(readers, JoinTimeout);
                    bool commitWriterDone = writer.Wait(JoinTimeout);
                    Check("commit readers finish within bound", commitReadersDone);
                    Check("commit writer finishes within bound", commitWriterDone);

                    if (commitReadersDone && commitWriterDone)
                    {
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
                }

                Baseline.TestAfterCandidateEstablished = null;

                if (Baseline.HasBaseline)
                {
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
                }
                else
                {
                    Check("third attempt precondition: baseline committed", false);
                }

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
