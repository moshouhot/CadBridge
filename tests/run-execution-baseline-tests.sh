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
dotnet "$DLL"
RC=$?
echo "exit_code=$RC"
exit $RC
