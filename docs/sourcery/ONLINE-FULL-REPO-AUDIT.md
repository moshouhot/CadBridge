# CadBridge online full-repository baseline audit

Status: **ONLINE COMPLETE / LOCAL VERIFICATION REQUIRED**

Audit head before this report: `8ab45cd`  
Scope: first-party source/config/test tooling under `src/`, `tools/`, `tests/`, plus `Directory.Build.props`.  
Excluded from source coverage: captured evidence bytes, generated build output, binaries, third-party SDK/runtime files.

This audit is intentionally split from local verification. No AutoCAD, CoreConsole, DAP adapter, COM session, or local Autodesk SDK build was executed by the online reviewer.

## Coverage ledger

All 49 first-party source/config files in the audited tree are accounted for.

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
9. Confirm no Autodesk reference assembly is copied into plugin output.

Do **not** run live CAD/DAP/COM probes merely to close this audit. Live execution needs a separate explicit safety decision and should use ownership-preserving harnesses.

## Closeout rule

The online stage is complete when the final head has been reviewed by Sourcery and no unresolved real finding remains.

The overall baseline remains **PARTIALLY COMPLETE** until the local non-live verification above is attached with exact command/results. Real AutoCAD behavior remains separately **UNVERIFIED** unless explicitly authorized and exercised later.
