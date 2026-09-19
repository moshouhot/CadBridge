#!/usr/bin/env bash
# CadBridge tooling self-tests.
#
# WHY: during P1 the harness silently produced empty evidence several times (path conversion,
# missing newline, decode failure, wrong CWD). Each time the CAD process exited 0 and the
# result looked like "the command was not registered" -- i.e. a tooling bug was one step away
# from being reported as a product finding.
#
# These tests assert the harness FAILS LOUDLY on each of those conditions, so a broken
# harness can never again masquerade as a negative product result.
#
# No AutoCAD is launched. Pure argument/path/decoder behaviour.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="$REPO/tools"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0
check() {
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "  PASS  $name"; pass=$((pass+1));
  else echo "  FAIL  $name"; fail=$((fail+1)); fi
}
expect_fail() {
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "  FAIL  $name (expected non-zero exit)"; fail=$((fail+1));
  else echo "  PASS  $name"; pass=$((pass+1)); fi
}

echo "== pathconv =="
check "to-win converts /f/a/b"        test "$(python "$TOOLS/pathconv.py" to-win '/f/a/b')" = 'F:\a\b'
check "to-msys converts F:\\a\\b"     test "$(python "$TOOLS/pathconv.py" to-msys 'F:\a\b')" = '/f/a/b'
check "to-win-slash converts /f/a/b"  test "$(python "$TOOLS/pathconv.py" to-win-slash '/f/a/b')" = 'F:/a/b'
expect_fail "unknown mode fails"      python "$TOOLS/pathconv.py" bogus '/f/a/b'

echo "== decoder =="
# UTF-16LE input must decode; UTF-8 input must also decode.
printf 'hello\x00w\x00o\x00r\x00l\x00d\x00' > "$TMP/u16.raw"
check "decodes UTF-16LE" bash -c "python '$TOOLS/decode-accoreconsole-output.py' '$(python "$TOOLS/pathconv.py" to-win "$TMP/u16.raw")' '$(python "$TOOLS/pathconv.py" to-win "$TMP/u16.txt")' && grep -q world '$TMP/u16.txt'"
printf 'plain ascii\n' > "$TMP/u8.raw"
check "decodes UTF-8" bash -c "python '$TOOLS/decode-accoreconsole-output.py' '$(python "$TOOLS/pathconv.py" to-win "$TMP/u8.raw")' '$(python "$TOOLS/pathconv.py" to-win "$TMP/u8.txt")' && grep -q ascii '$TMP/u8.txt'"
expect_fail "missing input fails" python "$TOOLS/decode-accoreconsole-output.py" "$(python "$TOOLS/pathconv.py" to-win "$TMP/nope.raw")" "$(python "$TOOLS/pathconv.py" to-win "$TMP/x.txt")"

echo "== accoreconsole harness: argument validation =="
expect_fail "missing --plugin rejected" bash "$TOOLS/run-accoreconsole-test.sh" --acad-dir /nonexistent --out-prefix "$TMP/x"
expect_fail "missing accoreconsole rejected" bash "$TOOLS/run-accoreconsole-test.sh" --acad-dir /nonexistent --plugin "$TOOLS/make-manifest.py" --out-prefix "$TMP/x"
expect_fail "missing plugin rejected" bash "$TOOLS/run-accoreconsole-test.sh" --acad-dir "$TOOLS" --plugin /nonexistent.dll --out-prefix "$TMP/x"

echo "== script generation: last line must be newline-terminated =="
# Regression: printf '%s' without \n concatenated the plugin path with the next command.
FAKE_ACAD="$TMP/fakeacad"; mkdir -p "$FAKE_ACAD"
cat > "$FAKE_ACAD/accoreconsole.exe" <<'EOF'
#!/usr/bin/env bash
# Emit the script path as our "raw output" so the caller can inspect it.
for ((i=1;i<=$#;i++)); do
  if [ "${!i}" = "/s" ]; then j=$((i+1)); cp "${!j}" "${!j%.scr}.raw"; fi
done
# Generic dap-probe is allowed to probe the adapter offline, but any attach/program/acad.exe
# mode must route through the safety gate.
if ! grep -q 'live_intent' "$TOOLS/dap-probe.py" || ! grep -q 'require_safety_review_passed' "$TOOLS/dap-probe.py"; then
  echo "  FAIL  dap-probe live mode is not safety-gated"
  GATE_VIOLATIONS=$((GATE_VIOLATIONS+1))
fi
# The legacy full-GUI shell harness is also a live entrypoint and must be frozen.
if ! grep -q 'safe_process.py.*--gate' "$TOOLS/run-acad-gui-test.sh"; then
  echo "  FAIL  run-acad-gui-test.sh is not safety-gated"
  GATE_VIOLATIONS=$((GATE_VIOLATIONS+1))
fi
exit 0
EOF
chmod +x "$FAKE_ACAD/accoreconsole.exe"
touch "$TMP/fake.dll"
bash "$TOOLS/run-accoreconsole-test.sh" --acad-dir "$FAKE_ACAD" --plugin "$TMP/fake.dll" \
  --out-prefix "$TMP/gen" --command CMD_ONE --command CMD_TWO >/dev/null 2>&1
if [ -f "$TMP/gen.scr" ]; then
  if [ "$(wc -l < "$TMP/gen.scr")" -eq 4 ] && tail -c1 "$TMP/gen.scr" | od -c | grep -q '\\n'; then
    echo "  PASS  script has 4 newline-terminated lines"; pass=$((pass+1))
  else
    echo "  FAIL  script line count/newline wrong:"; sed -n l "$TMP/gen.scr" | sed 's/^/        /'; fail=$((fail+1))
  fi
else
  echo "  FAIL  script not generated"; fail=$((fail+1))
fi

echo "== manifest validation =="
mkdir -p "$TMP/ev"
echo '{"ok":true}' > "$TMP/ev/good.json"
cat > "$TMP/ev/status.json" <<'EOF'
{"tests":[{"id":"T1","status":"PASS","evidence":["good.json"]}]}
EOF
check "valid manifest accepted" python "$TOOLS/make-manifest.py" "$TMP/ev" --phase X --run-id R --status-file "$TMP/ev/status.json"
cat > "$TMP/ev/bad-status.json" <<'EOF'
{"tests":[{"id":"T2","status":"PENDING_SUBAGENT","evidence":[]}]}
EOF
expect_fail "non-vocabulary status rejected" python "$TOOLS/make-manifest.py" "$TMP/ev" --phase X --run-id R --status-file "$TMP/ev/bad-status.json"
cat > "$TMP/ev/bad-path.json" <<'EOF'
{"tests":[{"id":"T3","status":"PASS","evidence":["does-not-exist.json"]}]}
EOF
expect_fail "missing evidence path rejected" python "$TOOLS/make-manifest.py" "$TMP/ev" --phase X --run-id R --status-file "$TMP/ev/bad-path.json"
printf '' > "$TMP/ev/empty.json"
cat > "$TMP/ev/empty-status.json" <<'EOF'
{"tests":[{"id":"T4","status":"PASS","evidence":["empty.json"]}]}
EOF
expect_fail "empty JSON artifact rejected" python "$TOOLS/make-manifest.py" "$TMP/ev" --phase X --run-id R --status-file "$TMP/ev/empty-status.json"

echo "== safety: no unverified process termination in tooling =="
# An earlier round cleaned up leftover CAD with `Get-Process acad | Stop-Process -Force`,
# which terminates EVERY process with that name and can close a user's CAD session. This
# check makes that class of mistake impossible to reintroduce silently: every tool script
# must route cleanup through safe_process.terminate_owned (pid + creation time + exe path),
# and no script may terminate by image name.
SAFE_VIOLATIONS=0
for f in "$TOOLS"/*.py; do
  base="$(basename "$f")"
  # safe_process.py itself is allowed to contain the taskkill primitive, but only by PID.
  [ "$base" = "safe_process.py" ] && continue
  if grep -qE 'Stop-Process|taskkill[^\n]*/IM' "$f"; then
    echo "  FAIL  by-name termination found in $base"; SAFE_VIOLATIONS=$((SAFE_VIOLATIONS+1))
  fi
  if grep -qE 'taskkill' "$f" && ! grep -qE 'sp\.terminate_owned|safe_process' "$f"; then
    echo "  FAIL  raw taskkill without safe_process ownership check in $base"
    SAFE_VIOLATIONS=$((SAFE_VIOLATIONS+1))
  fi
  if grep -qE 'win32com\.client\.Dispatch' "$f"; then
    echo "  FAIL  COM Dispatch fallback in $base (can start/select an unintended instance)"
    SAFE_VIOLATIONS=$((SAFE_VIOLATIONS+1))
  fi
done
if [ "$SAFE_VIOLATIONS" -eq 0 ]; then
  echo "  PASS  no unverified termination or COM Dispatch fallback in tooling"
else
  fail=$((fail+SAFE_VIOLATIONS))
fi
check "safe_process self-test passes" python "$TOOLS/safe_process.py" --self-test
check "repl probe offline tests pass (fakes, no CAD)" python "$TOOLS/test-repl-probe-offline.py"
check "repl probe offline tests pass (fakes, no CAD)" python "$TOOLS/test-repl-probe-offline.py"

# Raising SystemExit inside a `finally` replaces any in-flight exception with that exit
# code, so a run that crashed before its mandatory checks could still report a derived
# status and lose its traceback. Every such raise must be guarded by sys.exc_info().
SYS_EXIT_VIOLATIONS=0
for f in "$TOOLS"/*.py; do
  base="$(basename "$f")"
  if python - "$f" <<'PYEOF'
import ast, sys, pathlib
src = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
tree = ast.parse(src)
for node in ast.walk(tree):
    if isinstance(node, ast.Try) and node.finalbody:
        body = ast.unparse(ast.Module(body=node.finalbody, type_ignores=[]))
        if "SystemExit" in body and "exc_info" not in body:
            sys.exit(1)
sys.exit(0)
PYEOF
  then :; else
    echo "  FAIL  unguarded SystemExit in a finally block in $base"
    SYS_EXIT_VIOLATIONS=$((SYS_EXIT_VIOLATIONS+1))
  fi
done
if [ "$SYS_EXIT_VIOLATIONS" -eq 0 ]; then
  echo "  PASS  no unguarded SystemExit inside finally blocks"
else
  fail=$((fail+SYS_EXIT_VIOLATIONS))
fi

# A harness that rediscovers "the" CAD process by scanning the install directory and taking
# found[0] can select a process the user started. The PID must come from the launched handle.
ADOPTION_VIOLATIONS=0
for f in "$TOOLS"/*.py; do
  base="$(basename "$f")"
  [ "$base" = "safe_process.py" ] && continue
  if grep -nE 'found\[0\]' "$f" >/dev/null 2>&1; then
    echo "  FAIL  directory-scan PID adoption (found[0]) in $base"
    ADOPTION_VIOLATIONS=$((ADOPTION_VIOLATIONS+1))
  fi
done
if [ "$ADOPTION_VIOLATIONS" -eq 0 ]; then
  echo "  PASS  no directory-scan PID adoption in tooling"
else
  fail=$((fail+ADOPTION_VIOLATIONS))
fi

# Ownership must be a private-registry token, never a caller-supplied record. A dict handed
# to terminate_owned() cannot establish ownership, so no harness may build one.
TOKEN_VIOLATIONS=0
for f in "$TOOLS"/*.py; do
  base="$(basename "$f")"
  [ "$base" = "safe_process.py" ] && continue
  if grep -nE '\.record\(\)' "$f" >/dev/null 2>&1; then
    echo "  FAIL  caller builds an ownership record in $base (must pass a registry token)"
    TOKEN_VIOLATIONS=$((TOKEN_VIOLATIONS+1))
  fi
done
if [ "$TOKEN_VIOLATIONS" -eq 0 ]; then
  echo "  PASS  no caller-built ownership records"
else
  fail=$((fail+TOKEN_VIOLATIONS))
fi

# Live CAD harnesses must be gated while the safety review has not passed.
# EVERY script that can cause a live CAD session must be gated, not only the COM ones: the
# launch-topology harnesses make the adapter spawn AutoCAD, which is equally a live session.
GATE_VIOLATIONS=0
for f in "$TOOLS"/t01-5-*.py "$TOOLS"/dap-attach-*.py "$TOOLS"/dap-session.py "$TOOLS"/dap-a14-sequence.py; do
  [ -f "$f" ] || continue
  base="$(basename "$f")"
  if ! grep -q 'require_safety_review_passed' "$f"; then
    echo "  FAIL  live CAD harness not gated: $base"
    GATE_VIOLATIONS=$((GATE_VIOLATIONS+1))
  fi
done
# No environment variable may bypass the gate.
if grep -rn 'CBRIDGE_ACK' "$TOOLS"/*.py 2>/dev/null | grep -vE '^\s*#|let automation bypass' | grep -q .; then
  echo "  FAIL  a gate override still exists in code"
  GATE_VIOLATIONS=$((GATE_VIOLATIONS+1))
fi
if [ "$GATE_VIOLATIONS" -eq 0 ]; then
  echo "  PASS  every live CAD harness is gated on the safety review"
else
  fail=$((fail+GATE_VIOLATIONS))
fi

# The gate must actually refuse by default (negative test: no CAD is launched).
if python "$TOOLS/safe_process.py" --self-test >/dev/null 2>&1; then
  if python - <<'PYEOF'
import sys, pathlib
sys.path.insert(0, str(pathlib.Path("tools").resolve()))
import safe_process as sp
try:
    sp.require_safety_review_passed("selftest")
except sp.SafetyReviewNotPassed:
    sys.exit(0)
sys.exit(1)
PYEOF
  then
    echo "  PASS  safety gate refuses live harnesses by default"
  else
    echo "  FAIL  safety gate did not refuse"
    fail=$((fail+1))
  fi
else
  echo "  FAIL  safe_process self-test failed; skipping gate check"
  fail=$((fail+1))
fi

echo
echo "self-tests: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
