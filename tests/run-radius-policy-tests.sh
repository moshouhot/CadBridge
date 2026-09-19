#!/usr/bin/env bash
# Build and run the host-independent RadiusPromptPolicy tests.
#
# The tests exercise PURE decision logic against the real Autodesk PromptStatus enum. They
# start no AutoCAD and create no geometry; see the csproj header for what they do NOT prove.
#
# accoremgd.dll defines the PromptStatus type, so the CLR must resolve it at run time. The
# Autodesk assemblies are compile-time references only (Private=false) and are NEVER
# committed, so this script copies the one assembly the test needs next to the test binary in
# the build output directory (which is gitignored). Nothing is written back into the repo.
#
# Usage: run-radius-policy-tests.sh [--ref-dir DIR]
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PROJ="$REPO/tests/RadiusPromptPolicyTests/RadiusPromptPolicyTests.csproj"
REF_DIR="${CadBridgeLegacyRefDir:-D:\\CAD APPLOAD\\ObjectARX\\ObjectARX-2014\\inc}"

while [ $# -gt 0 ]; do
  case "$1" in
    --ref-dir) REF_DIR="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

command -v dotnet >/dev/null 2>&1 || { echo "ERROR: dotnet not found" >&2; exit 2; }
[ -f "$REF_DIR/accoremgd.dll" ] || {
  echo "ERROR: accoremgd.dll not found in '$REF_DIR'." >&2
  echo "Pass --ref-dir <ObjectARX inc dir> or set CadBridgeLegacyRefDir." >&2
  exit 2
}

echo "== build =="
dotnet build "$PROJ" -c Release -p:CadBridgeLegacyRefDir="$REF_DIR" -v m || exit $?

OUT="$REPO/tests/RadiusPromptPolicyTests/bin/Release/net48"
[ -f "$OUT/CadBridge.Tests.RadiusPromptPolicy.exe" ] || {
  echo "ERROR: test binary not produced at $OUT" >&2; exit 2; }

# Supply the referenced assembly at run time only. Never committed, never packaged.
cp -f "$REF_DIR/accoremgd.dll" "$OUT/" || { echo "ERROR: could not stage accoremgd.dll" >&2; exit 2; }

echo
echo "== run =="
# net48 output is a Windows executable; invoke it directly so the .NET Framework loader is
# used (running it via `dotnet` would treat it as a .NET Core app and fail on hostpolicy).
"$OUT/CadBridge.Tests.RadiusPromptPolicy.exe"
RC=$?
echo "exit_code=$RC"
exit $RC
