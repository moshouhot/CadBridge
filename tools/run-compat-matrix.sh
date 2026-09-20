#!/usr/bin/env bash
# CadBridge P1/T01-1 + T01-6 compatibility matrix.
#
# Loads each built plugin shell into every host that ships accoreconsole.exe and records:
#   - whether the commands register at all,
#   - what runtime the plugin actually reports (never a build constant),
#   - whether a circle was created and the same host subsequently reports the expected entity count.
#
# This is the evidence for A19's "one shared source tree, two shells, no per-year copies"
# claim, and it also discovers which year range the net48/net8 split really covers.
#
# HONEST LIMITS (recorded, not hidden):
#   * accoreconsole is not the GUI. It evidences load + core database/transaction behaviour.
#     It cannot evidence editor UI, view capture, zoom, or debugger attach.
#   * A host without accoreconsole.exe is NOT_RUN for this method, never FAIL.
#   * A host that aborts for an environment reason (e.g. a broken support path) is recorded
#     with that reason; it is not silently reported as an incompatible plugin.
set -u
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

# --- path helpers (pure bash) --------------------------------------------------------------
# NOTE: do NOT shell out to Windows python for this. Windows python.exe cannot open
# MSYS-style paths like /f/..., so it fails silently and the caller ends up with an empty
# path. Keep conversion in bash where both spellings are just strings.
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

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="$REPO/tools"
OUTROOT="$(to_msys "${1:?usage: run-compat-matrix.sh <out-dir>}")"
mkdir -p "$OUTROOT"

LEGACY_DLL="$REPO/src/CadBridge.Plugin.Legacy/bin/Release/net48/CadBridge.Plugin.Legacy.dll"
MODERN_DLL="$REPO/src/CadBridge.Plugin.Modern/bin/Release/net8.0-windows/CadBridge.Plugin.Modern.dll"
[ -f "$LEGACY_DLL" ] || { echo "ERROR: build the Legacy shell first" >&2; exit 2; }
[ -f "$MODERN_DLL" ] || { echo "ERROR: build the Modern shell first" >&2; exit 2; }

HOSTS=(
  "2016|/d/Program Files/Autodesk/AutoCAD 2016"
  "2017|/d/Program Files/Autodesk/AutoCAD 2017"
  "2018|/d/Program Files/Autodesk/AutoCAD 2018"
  "2020|/d/Program Files/Autodesk/AutoCAD 2020"
  "2022|/d/Program Files/Autodesk/AutoCAD 2022.1.5/AutoCAD 2022"
  "2023|/d/Program Files/Autodesk/AutoCAD_2023.1.5/AutoCAD 2023"
  "2024.1.5|/d/Program Files/Autodesk/AutoCAD_2024.1.5/AutoCAD 2024"
  "2024.1.7|/d/Program Files/Autodesk/AutoCAD_2024.1.7/AutoCAD 2024"
  "2025|/d/Program Files/Autodesk/AutoCAD_2025.1.1/AutoCAD 2025"
  "2026|/d/Program Files/Autodesk/AutoCAD 2026"
)

SUMMARY="$OUTROOT/matrix-summary.tsv"
printf 'host\tshell\taccoreconsole\tloaded\truntime\tcircle_ok\tentities_after\tnote\n' > "$SUMMARY"

for entry in "${HOSTS[@]}"; do
  label="${entry%%|*}"; dir="${entry#*|}"
  for shell in legacy modern; do
    if [ "$shell" = "legacy" ]; then DLL="$LEGACY_DLL"; else DLL="$MODERN_DLL"; fi
    tag="${label}-${shell}"
    prefix="$OUTROOT/$tag"

    if [ ! -f "$dir/accoreconsole.exe" ]; then
      printf '%s\t%s\tNO\tNOT_RUN\t-\t-\t-\taccoreconsole.exe absent\n' "$label" "$shell" >> "$SUMMARY"
      echo "[$tag] NOT_RUN (no accoreconsole.exe)"
      continue
    fi

    out="$(bash "$TOOLS/run-accoreconsole-test.sh" \
      --acad-dir "$dir" --plugin "$DLL" --out-prefix "$prefix" \
      --command CBBRIDGEINFO --command CBBRIDGEPINGCIRCLE --command CBBRIDGEINFO 2>&1)"
    rc=$?
    txt="$(printf '%s\n' "$out" | sed -n 's/^DECODED_PATH=//p' | head -1)"
    [ -n "$txt" ] || txt="$prefix.txt"

    loaded="NO"; runtime="-"; circle="-"; entities="-"; note=""
    if [ -f "$txt" ]; then
      grep -q "CBBRIDGEINFO BEGIN" "$txt" && loaded="YES"
      runtime="$(grep -m1 'runtime_framework=' "$txt" | sed -e 's/^[[:space:]]*//' | tr -d '\r')"
      if grep -q 'CBBRIDGEPINGCIRCLE OK' "$txt"; then circle="YES"; fi
      entities="$(grep 'model_space_entities:' "$txt" | tail -1 | sed -e 's/.*model_space_entities:[[:space:]]*//' | tr -d '\r')"
      grep -q 'MessageBox' "$txt" && note="host-abort-dialog"
      grep -q 'ErrorStatus=53' "$txt" && note="host-errorstatus-53"
      if [ "$loaded" = "NO" ] && [ -z "$note" ]; then note="command-not-registered"; fi
    else
      note="no-decoded-output"
    fi

    printf '%s\t%s\tYES\t%s\t%s\t%s\t%s\t%s\n' \
      "$label" "$shell" "$loaded" "${runtime:--}" "$circle" "${entities:--}" "$note" >> "$SUMMARY"
    echo "[$tag] rc=$rc loaded=$loaded circle=$circle entities=${entities:--} $note"
  done
done

echo
echo "SUMMARY: $SUMMARY"
cat "$SUMMARY"
