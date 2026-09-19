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

echo
echo "self-tests: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
