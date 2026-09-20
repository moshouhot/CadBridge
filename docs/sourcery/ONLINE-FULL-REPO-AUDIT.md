# CadBridge online full-repository baseline audit

Status: **ONLINE COMPLETE / LOCAL VERIFICATION REQUIRED**

Audit head before this report: `8ab45cd`  
Scope: first-party source/config/test tooling under `src/`, `tools/`, `tests/`, plus `Directory.Build.props`.  
Excluded from source coverage: captured evidence bytes, generated build output, binaries, third-party SDK/runtime files.

This audit is intentionally split from local verification. No AutoCAD, CoreConsole, DAP adapter, COM session, or local Autodesk SDK build was executed by the online reviewer.

## Coverage ledger

All 53 first-party source/config files in the current audited tree are accounted for.

### Product/shared C# — deep online review; local build/runtime required
- `src/Plugin.Shared/DiagnosticsCommands.cs`
- `src/Plugin.Shared/ExecutionContextBaseline.cs`
- `src/Plugin.Shared/LiveReadLispFunction.cs`
- `src/Plugin.Shared/RadiusPromptPolicy.cs`
- `src/Plugin.Shared/RuntimeInfo.cs`
- `src/CadBridge.Plugin.Legacy/CadBridge.Plugin.Legacy.csproj`
- `src/CadBridge.Plugin.Modern/CadBridge.Plugin.Modern.csproj`
- `Directory.Build.props`

### Test C# / project files — online contract review; local compile/run required
- `tests/CoreConsoleProbe/CoreConsoleProbe.csproj`
- `tests/CoreConsoleProbe/ProbeCommands.cs`
- `tests/CoreConsoleProbeModern/CoreConsoleProbeModern.csproj`
- `tests/RadiusPromptPolicyTests/PolicyTests.cs`
- `tests/RadiusPromptPolicyTests/RadiusPromptPolicyTests.csproj`
- `tests/run-radius-policy-tests.sh`
- `tests/ExecutionContextBaselineTests/ExecutionContextBaselineTests.csproj`
- `tests/ExecutionContextBaselineTests/AutodeskStubs.cs`
- `tests/ExecutionContextBaselineTests/Program.cs`
- `tests/run-execution-baseline-tests.sh`

### Process ownership / worker boundary — deep online review
- `tools/safe_process.py`
- `tools/bounded_worker.py`
- `tools/com_read_worker.py`
- `tools/job-family-fixture.py`
- `tools/live-job-ownership-smoke.py`

### DAP / live-probe tooling — deep or targeted online review; live execution remains gated
- `tools/dap-probe.py`
- `tools/dap-session.py`
- `tools/dap-a14-sequence.py`
- `tools/dap-attach-a14.py`
- `tools/dap-attach-matrix.py`
- `tools/dap-attach-ordering.py`
- `tools/t01-5-definitive.py`
- `tools/t01-5-pause-live-query.py`
- `tools/t01-5-pause-query-probe.py`
- `tools/t01-5-plugin-pause-read.py`
- `tools/t01-5-repl-plugin-read.py`
- `tools/t01-5-sync-read-forms.py`
- `tools/test-repl-probe-offline.py`

### Evidence integrity / safety-gate tooling — deep online review
- `tools/check-live-gates.py`
- `tools/make-manifest.py`
- `tools/redact-evidence.py`
- `tools/selftest.sh`

### CoreConsole / compatibility / decoding / path tooling — online review
- `tools/run-accoreconsole-test.sh`
- `tools/run-compat-matrix.sh`
- `tools/run-acad-gui-test.sh`
- `tools/decode-accoreconsole-output.py`
- `tools/pathconv.py`

### Read-only inventory / comparison / static localization — online review
- `tools/capture-cad-security-baseline.ps1`
- `tools/capture-env.ps1`
- `tools/compare-security-baseline.py`
- `tools/diagnose-refpath.ps1`
- `tools/inventory-autocad.ps1`
- `tools/probe-debug-adapter.py`
- `tools/scan-autocad.ps1`
- `tools/scan-sdk-assemblies.ps1`

## Confirmed online findings and fixes

### O1 — HIGH — direct launch could become an unmanaged orphan
`safe_process.launch_and_record()` returned `None` after identity-enumeration failure, wrong executable identity, or verification timeout without cleaning the exact process it had just started. The caller then had no ownership token with which to clean it.

Fixed in `2164f00`: failed direct launches are cleaned only through the retained `Popen` handle, never by PID/name guessing. Regression coverage was added for wrong-exe, enumeration-failure and timeout paths.

### O2 — HIGH — trusted execution baseline was mutable
The idle execution baseline was exported as Lisp function `CBBASELINE`, even though the code itself warned that recording it while Lisp was paused would defeat the context check. Removing the Lisp export reduced the attack surface, but the command setter could still overwrite a successful baseline later.

Fixed in `2164f00` and `7554677`: the setter is command-only (`CBBRIDGEBASELINE`) and a successfully established baseline is immutable for the session. Failed startup attempts may retry.

### O3 — HIGH (evidence integrity) — validation harnesses could FAIL and exit 0
Several DAP/T01 validation scripts recorded failed checks but still returned process exit code 0, allowing shell/CI callers to treat a failed experiment as successful.

Fixed in `8b4d852` and `12ffa9a`: validation finalizers derive nonzero exit status from recorded failures without masking an exception already in flight.

Affected:
- `dap-a14-sequence.py`
- `dap-attach-a14.py`
- `dap-session.py`
- `t01-5-pause-live-query.py`
- `t01-5-pause-query-probe.py`
- `t01-5-sync-read-forms.py`

Exploratory matrix scripts that intentionally collect outcomes rather than assert one expected result were not mechanically converted into pass/fail harnesses.

### O4 — MEDIUM — compatibility runtime parser drifted from product output
`run-compat-matrix.sh` searched for a historical `runtime:` line, while current `CBBRIDGEINFO` emits `runtime_framework=...` inside `RuntimeInfo.Summary()`. The script could therefore record `-` despite a valid runtime report. Its header also overstated same-process entity counting as an independent reopen/readback.

Fixed in `8b4d852`: parse the current measured runtime summary and state the actual same-host evidence boundary.

### O5 — HIGH (evidence integrity) — tests could cite evidence outside the run
Manifest test validation accepted any existing `root / rel` path. A status file could cite `../outside.txt` or an absolute path and receive PASS even though that file was outside the run and not hashed into the manifest.

Fixed in `e7ed15c`: PASS/FAIL evidence must be a non-empty relative path, contain no parent traversal, and be present in the manifest's own hashed artifact set. Negative tests cover existing traversal and absolute targets.

### O6 — MEDIUM — runtime framework classification could false-negative
`RuntimeInfo.IsFrameworkClr` gated classification on an unqualified `Type.GetType` lookup even though `RuntimeInformation` is already a compile-time dependency. On .NET Framework that lookup can return null for a type outside corelib and incorrectly report `framework_clr=false`.

Fixed in `ee7da69`: classify directly from the measured `RuntimeInformation.FrameworkDescription`.

### O7 — HIGH (evidence integrity) — --extra could replace protected manifest fields
`make-manifest.py` used `manifest.update(extra)`, allowing a metadata file to replace `artifacts`, `tests`, `validation`, run identity and other derived fields. The later check only repaired `artifact_count`, so forged artifact hashes or `validation.ok=true` could survive.

Fixed in `8ab45cd`: `--extra` is metadata-only. Attempts to override protected fields are ignored, recorded as validation failures, and produce a nonzero result. Regression coverage attempts to forge both `artifacts` and `validation`.

## Post-audit non-live verification follow-up

Independent local verification of `b715501c4604ed4a5a5da9b32737badc70dd646d`
returned **LOCAL NON-LIVE FAIL** despite 125 passing checks, because pyflakes found two
undefined-name regressions introduced by the O3 exit-code remediation.

- `dap-a14-sequence.py` computed `failed` but tested undefined `bad`.
- `t01-5-pause-query-probe.py` tested undefined `bad` without computing a failed count.
- `selftest.sh` did not run repository-wide compile/pyflakes gates, so it could remain green
  while changed Python harnesses contained undefined names.

The follow-up remediation fixes both exit paths and makes whole-tools `compileall` and
`pyflakes` part of `selftest.sh`. The prior local results are evidence for `b715501`
only; the new head requires a fresh full non-live verification.

The final Sourcery review of `b715501` also found two valid gaps that are fixed in the same
follow-up: `redactions` provenance is now protected from `--extra` replacement, and
execution-context baseline readers consume a snapshot synchronized with the one-shot writer.

## Second local verification follow-up: readiness publication defect

Independent local verification of `1719bb6e4ef252375adfadc3ca98020394df8295`
ran 337 non-live checks without a test failure but correctly returned **LOCAL NON-LIVE FAIL**
because one Sourcery thread remained open.

The open finding showed that the one-shot baseline was committed before
`CB_BASELINE_LOG_PATH` was written. A transient readiness I/O failure could therefore leave
`HasBaseline == true` while no readiness file existed, and every retry was refused as
`already_recorded`.

The remediation makes baseline establishment plus readiness publication a lock-scoped
transaction. If readiness publication fails for a baseline established by that attempt, the
candidate is cleared before the lock is released, so a retry is allowed and `Check()` cannot
observe the transient state.

A durable non-live test now compiles the production
`ExecutionContextBaseline.cs` against only an Autodesk host-boundary stub and verifies:

- readiness I/O failure propagates;
- failed publication leaves the baseline unset;
- a second attempt with a writable path succeeds;
- a third attempt remains one-shot and is refused;
- concurrent readers never observe an unpublished/torn baseline.

The previous handoff also overstated the review-thread state. At that time GitHub actually
reported 15 total / 14 resolved / 1 unresolved even though the Sourcery check was green.
Baseline closeout therefore requires BOTH a successful Sourcery check and zero unresolved
review threads.

## Third local verification follow-up: test harness defects

Independent local verification of `1e119587d6740d971422ef64afd07aa32a067e0d`
returned **LOCAL NON-LIVE FAIL** even though the production F1 transaction itself was
independently reproduced as correct.

Two test defects were confirmed:

- F3: the new regression did not compile because its test namespace ended in
  `ExecutionContextBaseline`, shadowing the imported production type. The regression also was
  not wired into `tools/selftest.sh`, so the main self-test could remain green.
- F4: the concurrency section was vacuous. It started readers only after the one-shot baseline
  had already committed, so all later writers were refused and no reader raced a publication
  transition. A temporary mutation removing the publication lock still passed.

The repaired regression now:

- uses a non-conflicting namespace and an explicit global alias for the production type;
- is a mandatory `selftest.sh` gate;
- compiles the real production `ExecutionContextBaseline.cs`;
- enables a `CADBRIDGE_TESTING`-only internal synchronization hook that is absent from normal
  plugin builds;
- starts readers before the first rollback and first successful commit;
- holds the writer after a provisional candidate exists, proving readers cannot finish until
  commit/rollback releases the publication lock;
- verifies rollback readers see only fully-unset state and commit readers see only fully-
  published state.

Acceptance of this repair requires the local mutation that removes the transaction lock to
make the concurrency regression fail; a passing no-lock mutation means the test is still
vacuous.

At this stage GitHub reported 16 review threads, 15 resolved and 1 unresolved (F4). As before,
a green Sourcery check is not sufficient without zero unresolved threads.

## Fourth local verification follow-up: bounded-failure harness

Independent local verification of `8074c60a9f132f8ff449d2d5c01350362f84e853`
proved the repaired concurrency test is meaningful: the no-lock mutation was killed with
three failures and exited nonzero. The production F1 transaction was not invalidated.

The same verification found F6: realistic rollback/ordering mutations produced failures but
could then hang indefinitely because several synchronization/join waits were unbounded. The
mandatory runner and therefore `tools/selftest.sh` could wedge instead of reporting failure.

This follow-up keeps production baseline semantics unchanged and hardens only the test
infrastructure:

- candidate-entry waits, hook release waits, reader joins and writer joins are bounded;
- timeout paths are explicit FAIL evidence rather than silent continuation;
- post-state checks are skipped when a bounded join itself fails, avoiding a second lock wait;
- `run-execution-baseline-tests.sh` adds an outer owned-process watchdog so an internal
  harness regression cannot wedge the parent self-test indefinitely.

F7 was an evidence-accuracy nit: `BASELINE-AUDIT.md` still said 47 self-tests although the
verified `8074c60` script ran 53. The current-measurement row is corrected without rewriting
older historical results.

At that stage GitHub reported 17 review threads, 16 resolved and 1 unresolved (F7).

### Independent AI review closeout

The project now treats Sourcery, Codex Code Review, and Codex Security Review as separate
audit surfaces. For a final candidate SHA, a green result from one does not substitute for the
others. Closeout requires evidence that the final SHA was actually covered by each enabled
surface, with all confirmed findings handled. If GitHub does not expose evidence binding a
Codex review/security result to the final SHA, that surface remains **UNVERIFIED** rather than
being inferred from configuration.

## Fifth online follow-up: confirmed Codex review findings

Codex Code Review was enabled as a formal independent audit surface and reviewed
`8074c60a9f132f8ff449d2d5c01350362f84e853`. Its new findings were independently checked
against source rather than accepted mechanically. Eight were confirmed and remediated on the
online branch:

- repository-wide live-gate coverage now includes first-party scripts under `tests/` and the
  repository root, while captured historical evidence under `docs/evidence/` stays out of
  executable-source scope;
- Python gate calls must be reachable from the executable `main()` path; dead branches and
  uncalled helpers cannot satisfy the checker;
- `com_read_worker.py` is now a gated live entrypoint because scalar identity verification
  does not prove ownership or authorize attaching to an arbitrary existing AutoCAD process;
- explicit missing redaction paths fail closed instead of reporting a zero-file clean result;
- identifier-aware UTF-16LE/BE matching closes the Chinese-heavy BOM-less UTF-16 false-clean
  case before GB18030 fallback;
- redaction stages both evidence and provenance before publication and publishes provenance
  first, so a sidecar creation failure cannot occur after evidence bytes are changed;
- atomic manifest replacement preserves the existing destination permission mode;
- manifest validation is recomputed after all `--extra` handling so non-object metadata cannot
  leave `validation.ok=true` alongside a recorded problem.

The previous F6 bounded-failure harness and F7 evidence-history correction from `9cb4ab03`
are retained. Because the self-test suite changed again in this follow-up, the 53-pass count
remains historical evidence for `8074c60`; the final candidate requires a fresh local run and
must not guess a new count.

Codex Code Review, Codex Security Review and Sourcery remain independent closeout surfaces.
Evidence must bind each enabled review to the final SHA; otherwise that surface is reported
**UNVERIFIED**.

## Sixth online follow-up: final Codex completion findings

Codex Code Review and Codex Security Review both completed against
`c0412ea41980a6ef4a9286ceded657103daee225`. Completion was not treated as a clean pass:
the code review added six unresolved findings, all independently reproduced and confirmed.

This follow-up addresses them without authorizing any live CAD execution:

- shell behavioural discovery skips heredoc payload bodies, which are data written to fixtures
  rather than commands executed by `selftest.sh`;
- a module-level safety gate is accepted only as a direct top-level barrier before the
  conventional `__main__` entrypoint, so a gate appended after `main()` cannot pass review;
- the baseline rollback guard now begins before candidate establishment, covering exceptions
  raised after provisional fields are set but before the readiness string returns;
- redaction returns nonzero in scrub and dry-run modes too whenever any intended target could
  not be processed;
- newly-created provenance sidecars inherit the evidence permission mode instead of publishing
  the `mkstemp` default 0600 mode;
- no-path redaction anchors tracked-file enumeration to the CadBridge repository containing
  the tool rather than the caller's current working directory.

Each defect class has a non-live regression or mutation test. Because this produces another
candidate SHA, all Sourcery/Codex results for `c0412ea` remain historical evidence only and
the final SHA must be reviewed again.

## Seventh online follow-up: completed b899b7d reviews

Sourcery, Codex Code Review and Codex Security Review all completed against
`b899b7dad7fdc9b405fee71b578db13852ebef4d`. Completion again was not treated as a clean
pass: six open findings were independently reproduced and confirmed.

This follow-up addresses them:

- compatibility parsing strips the `runtime_framework=` key and stores only the measured
  runtime value;
- Python live-gate review now requires the gate barrier to precede every known adapter/process/
  COM live sink in `main()`, not merely exist somewhere reachable;
- carried `redactions.artifacts` provenance is cross-checked against freshly hashed artifact
  paths, sizes and `stored_sha256` values before validation can report success;
- a UTF-16 identifier byte pattern followed by malformed UTF-16 is fail-closed and cannot fall
  through to GB18030 as a false clean;
- redaction refuses target filenames containing the private identifier instead of repeating
  that identifier into the provenance sidecar;
- execution-baseline readiness is staged, flushed and atomically published in the destination
  directory so consumers never see a partial readiness record.

Non-live regression/mutation coverage was added for each class. The resulting candidate SHA
must undergo Sourcery + Codex Code Review + Codex Security Review again before local final
verification.

## Eighth online follow-up: completed 4a162e2 reviews

Codex Code Review and Codex Security Review completed against
`4a162e20405895afcbf4f6759450247956fcd7af`. Seven open threads remained after
completion. They were independently reproduced; the two gate-order findings overlap but
exercise different bypasses.

This follow-up strengthens the same non-live boundaries:

- conditional live-intent polarity is fail-closed: only simple expressions whose truth value
  for `live_intent=true` can be statically proven are accepted; compound/ambiguous guards are
  rejected;
- gate ordering compares the actual gate-call line with known live sinks, so a sink inserted
  before the gate inside the same conditional branch fails review;
- each Python gate call must pass the exact current harness key, preventing one harness from
  borrowing another future allowlist entry;
- compatibility parsing extracts `runtime_framework` from its real semicolon-delimited
  summary position;
- redaction detects case-insensitive identifier variants and refuses unsafe partial rewrites;
- every publication-relative path component is checked for the identifier, not only the
  basename;
- symlink evidence targets are refused before replacement so repository structure and referents
  cannot diverge silently.

All changes remain non-live. The resulting SHA requires another full Sourcery + Codex Code
Review + Codex Security Review cycle before local final verification.

## Ninth online follow-up: completed 1663552 reviews

Sourcery, Codex Code Review and Codex Security Review completed against
16635528d65059bc2cbaa38b9d7fe7fadadec882. Five new Codex findings were independently
reproduced and addressed:

- fresh atomic manifests now publish with normal POSIX file permissions derived from the
  process umask rather than retaining the temporary-file private mode;
- conditional Python gates must be direct statements of the proven live-intent branch, so an
  optional nested condition cannot leave another live path unauthorized;
- shell gate verification parses a real Python execution of safe_process.py --gate and the
  exact harness key; echoed text cannot satisfy review;
- PowerShell discovery treats a direct command such as acad.exe /nologo as a live launch;
- redaction replaces only byte spans that correspond one-for-one with decoded occurrences;
  UTF-16 byte coincidences at odd offsets are not modified.

Matching non-live regression and mutation coverage was added for each class. The resulting SHA
requires another three-surface review cycle before final local verification.

## Tenth online follow-up: final owner-directed remediation

The three configured audit surfaces completed against
`95d382786685e7709317940d62d06dd398a64949`. Their final completed pass produced nine
additional open findings. All nine were independently reproduced and remediated in this
follow-up:

- an explicitly requested status file must exist and parse as the expected object/list shape;
- conditional live authorization must not sit beneath any optional ancestor guard;
- Python class/function/assignment-expression/exception bindings can invalidate a trusted gate
  import and are treated as rebindings;
- atomic manifest generation refuses a symlink destination;
- stale `manifest.json.*.tmp` staging files are validation failures, never evidence artifacts;
- the three T01-5 harnesses that ignored `terminate_owned()` now record cleanup as a check so
  cleanup failure forces a non-zero final status;
- unsupported binary/container evidence formats fail closed instead of counting as clean;
- a successful scrub is not publishable while the identifier remains visible under another
  supported encoding;
- the project COM attach wrapper `com_attach_existing` is treated as a live sink by both
  behavioural discovery and gate-order analysis.

Per project-owner direction, this remediation is NOT followed by another Codex review cycle.
The next certification step is a fresh independent local non-live verification of the exact
post-remediation SHA. Live AutoCAD/CoreConsole/DAP/COM execution remains UNVERIFIED unless
separately authorized.

## Eleventh follow-up: local non-live FAIL cleanup

Independent local verification of `ded8518cac98ecbc42f59480e1ec9661609946d9`
correctly returned **LOCAL NON-LIVE FAIL**. The core baseline/build regressions remained healthy,
but four closeout defects were confirmed and are fixed in this follow-up:

- the `SKIP_SUFFIXES` -> `UNSCANNABLE_SUFFIXES` policy rename left one stale symbol in the
  result-aggregation path, causing a NameError instead of the intended fail-closed diagnostic;
- four remaining live harnesses still discarded `terminate_owned()` results; all now persist
  cleanup confirmation and make cleanup failure affect the final process status;
- mixed-encoding dry-run now validates the simulated transformed bytes exactly like real scrub,
  and the regression asserts the intended refusal message while explicitly rejecting
  Traceback/NameError false positives;
- a pre-existing provenance sidecar symlink is refused before read/stat/atomic replacement, so
  the scrub cannot silently replace the link with a regular file.

The prior handoff's 58/58 thread count was a point-in-time statement. After automatic Codex
review of `ded8518`, GitHub reported 68 total / 58 resolved / 10 unresolved. Per owner
decision, that automatic post-handoff Codex cycle is not a closeout gate. One of those findings
(the provenance-sidecar symlink) was independently reproduced locally and therefore promoted
to a required fix above.

No further Codex review is requested for this remediation. The next certification step is a
fresh independent local non-live verification of the exact new SHA.

## Twelfth follow-up: G5 mixed-encoding fixture parity

Independent local verification of `d638b4952a0acd043503cb96b0db058702702372`
confirmed all prior product blockers fixed, but found the mixed-encoding regression fixture itself
depended on identifier-length parity. For odd identifier lengths the concatenated bytes had odd
length and therefore took the malformed-UTF-16 refusal path instead of the intended
partial-scrub refusal path.

This follow-up changes only the non-live test fixture:

- mixed-encoding payload construction pads one byte when necessary so the complete fixture is
  always valid UTF-16LE regardless of identifier length;
- construction asserts both UTF-16LE and UTF-8 identifier representations are present;
- a dedicated regression exercises 29-character and 30-character synthetic identifiers through
  both dry-run and real scrub and requires the exact
  `refusing to publish a partial scrub` reason.

No private machine identifier is embedded in the repository. Product redaction logic is
unchanged. No further Codex review is requested; the next certification step remains fresh
local non-live verification of the resulting exact SHA.

## Important limits / not silently approved

### L1 — launch-topology DAP ownership
Legacy launch-topology probes start the adapter with `-- <acad.exe>`; the adapter then owns/spawns the CAD host. `DapClient.close()` owns the adapter process, not a retained Windows Job Object containing the CAD family. These harnesses must not become approved merely by adding their names to a review allowlist. Prefer the reviewed Job Object ownership pattern before any future live enablement.

### L2 — no live AutoCAD claim
No online edit proves real AutoCAD load, prompt cancellation, DAP attach/step behavior, COM behavior, or cleanup against an Autodesk host. Those remain **UNVERIFIED** until local evidence is produced.

### L3 — Security Scan / Sourcery dashboard
GitHub PR review is connected and working. Repository-wide Sourcery Security Scan/dashboard settings are external service configuration and are not proven by this source audit.

## Local verification required before baseline closeout

Safe/non-live verification first:

1. Run `python tools/safe_process.py --self-test`.
2. Run `bash tools/selftest.sh`.
3. Run `python tools/test-repl-probe-offline.py`.
4. Run Python syntax/static checks used by the project (including `pyflakes tools/*.py`).
5. Run `bash -n` on modified shell scripts.
6. Build Legacy (`net48`) with the pinned local ObjectARX reference directory.
7. Build Modern (`net8.0-windows`) with the pinned local ObjectARX 2025 reference directory.
8. Run `tests/run-radius-policy-tests.sh`.
9. Run `tests/run-execution-baseline-tests.sh` and require the failed-publish/retry/one-shot/concurrency regression to pass.
10. Confirm no Autodesk reference assembly is copied into plugin output.

Do **not** run live CAD/DAP/COM probes merely to close this audit. Live execution needs a separate explicit safety decision and should use ownership-preserving harnesses.

## Closeout rule

The online stage is complete when the final head has been reviewed by Sourcery and no unresolved real finding remains.

The overall baseline remains **PARTIALLY COMPLETE** until the local non-live verification above is attached with exact command/results. Real AutoCAD behavior remains separately **UNVERIFIED** unless explicitly authorized and exercised later.
