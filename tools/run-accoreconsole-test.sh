#!/usr/bin/env bash
# CadBridge P1 harness: load a built plugin shell into a real AutoCAD Core Engine Console
# (accoreconsole.exe), run a command script, and capture decoded output as evidence.
#
# WHY THIS IS REAL EVIDENCE, NOT A MOCK:
#   accoreconsole.exe is the genuine AutoCAD core engine (the same acdbmgd/accoremgd core the
#   GUI uses). It is headless, so it never touches a user session, user drawing or user
#   profile. A plugin loaded here runs on the real CAD main thread inside a real
#   DocumentLock/Transaction.
#
# WHAT IT DOES NOT PROVE:
#   accoreconsole does not ship acmgd.dll (the UI-level managed assembly), so it cannot
#   evidence anything needing the editor UI, palettes, view capture or zoom. Those need a
#   full AutoCAD session and are tracked separately in the P1 test plan.
#
# Usage:
#   run-accoreconsole-test.sh --acad-dir DIR --plugin DLL --out-prefix PREFIX \
#       [--command CMD]... [--dwg FILE]
set -u

# MSYS/Git-Bash rewrites arguments starting with '/' into Windows paths, turning "/s" into
# "S:/" and making accoreconsole print Usage instead of running. Disable that.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

ACAD_DIR=""; PLUGIN=""; PREFIX=""; DWG=""
COMMANDS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --acad-dir)   ACAD_DIR="$2"; shift 2 ;;
    --plugin)     PLUGIN="$2";   shift 2 ;;
    --out-prefix) PREFIX="$2";   shift 2 ;;
    --dwg)        DWG="$2";      shift 2 ;;
    --command)    COMMANDS+=("$2"); shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

[ -n "$ACAD_DIR" ] && [ -n "$PLUGIN" ] && [ -n "$PREFIX" ] || {
  echo "usage: $0 --acad-dir DIR --plugin DLL --out-prefix PREFIX [--command CMD]... [--dwg FILE]" >&2; exit 2; }
[ -f "$ACAD_DIR/accoreconsole.exe" ] || { echo "ERROR: no accoreconsole.exe in $ACAD_DIR" >&2; exit 2; }
[ -f "$PLUGIN" ] || { echo "ERROR: plugin not found: $PLUGIN" >&2; exit 2; }

# Path helpers. Pure bash/sed on purpose: Windows python.exe cannot open MSYS-style paths
# such as /f/..., so delegating conversion to python fails silently and loses the evidence.
to_win() {
  local p="$1"
  case "$p" in
    [A-Za-z]:[\\/]*) : ;;
    /*)              p="$(printf '%s' "$p" | sed -e 's|^/\([a-zA-Z]\)/|\1:/|')" ;;
    *)               p="$(pwd)/$p" ;;
  esac
  printf '%s' "$p" | sed -e 's|/|\\|g'
}
to_msys() {
  local p="$1"
  case "$p" in
    [A-Za-z]:[\\/]*) p="$(printf '%s' "$p" | sed -e 's|^\([a-zA-Z]\):|/\1|' -e 's|\\|/|g')" ;;
    /*)              : ;;
    *)               p="$(pwd)/$p" ;;
  esac
  printf '%s' "$p"
}

mkdir -p "$(dirname "$PREFIX")"
SCR_LOCAL="$PREFIX.scr"
RAW="$PREFIX.raw"
TXT="$PREFIX.txt"
SCR_WIN="$(to_win "$PREFIX.scr")"
PLUGIN_WIN="$(to_win "$PLUGIN")"
RAW_WIN="$(to_win "$PREFIX.raw")"
TXT_WIN="$(to_win "$PREFIX.txt")"

# Build the command script. NETLOAD takes the assembly path on the following line. Every
# line must be newline-terminated including the last, or the final command is concatenated
# onto the plugin path.
{
  echo "NETLOAD"
  printf '%s\n' "$PLUGIN_WIN"
  for c in "${COMMANDS[@]}"; do printf '%s\n' "$c"; done
} > "$SCR_LOCAL"

ACAD_EXE="$ACAD_DIR/accoreconsole.exe"
if [ -n "$DWG" ]; then
  "$ACAD_EXE" /i "$(to_win "$DWG")" /s "$SCR_WIN" > "$RAW" 2>&1
else
  "$ACAD_EXE" /s "$SCR_WIN" > "$RAW" 2>&1
fi
RC=$?

# Absolute path to the decoder: the CAD process changes nothing about $0, but a relative
# dirname breaks when the caller's working directory differs from the repo root.
TOOLS_ABS="$(cd "$(dirname "$0")" && pwd)"
python "$(to_win "$TOOLS_ABS/decode-accoreconsole-output.py")" "$RAW_WIN" "$TXT_WIN" >/dev/null 2>&1
decode_rc=$?
if [ $decode_rc -ne 0 ]; then
  echo "WARNING: decode failed (rc=$decode_rc); raw output preserved at $RAW" >&2
fi

echo "exit_code=$RC"
echo "script=$SCR_LOCAL"
echo "raw=$RAW"
echo "decoded=$(to_msys "$TXT_WIN")"
echo "DECODED_PATH=$(to_msys "$TXT_WIN")"
exit $RC
