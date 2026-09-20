#!/usr/bin/env bash
# Non-live regression for ExecutionContextBaseline publication semantics.
# Compiles the production source with only the Autodesk host boundary stubbed.
# No AutoCAD/CoreConsole/DAP/COM process is launched.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PROJ="$REPO/tests/ExecutionContextBaselineTests/ExecutionContextBaselineTests.csproj"

command -v dotnet >/dev/null 2>&1 || { echo "ERROR: dotnet not found" >&2; exit 2; }

echo "== build =="
dotnet build "$PROJ" -c Release --no-incremental -v m || exit $?

OUT="$REPO/tests/ExecutionContextBaselineTests/bin/Release/net8.0-windows"
DLL="$OUT/CadBridge.Tests.ExecutionContextBaseline.dll"
[ -f "$DLL" ] || { echo "ERROR: test binary not produced at $DLL" >&2; exit 2; }

echo
echo "== run =="
# Second-layer watchdog: the C# harness has bounded waits, but a regression in the
# harness itself must not wedge selftest/CI forever. Python is already a project
# prerequisite and can terminate only this owned dotnet test process on timeout.
RUN_TIMEOUT_SECONDS="${CB_EXEC_BASELINE_TIMEOUT_SECONDS:-45}"
python - "$DLL" "$RUN_TIMEOUT_SECONDS" <<'PYEOF'
import subprocess
import sys

dll = sys.argv[1]
timeout_seconds = float(sys.argv[2])
try:
    completed = subprocess.run(["dotnet", dll], timeout=timeout_seconds)
except subprocess.TimeoutExpired:
    print(
        f"ERROR: execution-baseline regression timed out after {timeout_seconds:g}s",
        file=sys.stderr,
    )
    sys.exit(124)
sys.exit(completed.returncode)
PYEOF
RC=$?
echo "exit_code=$RC"
exit $RC
