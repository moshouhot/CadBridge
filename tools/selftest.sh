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
#
# NOTE: this fake exists ONLY to exercise argument validation and script generation in
# run-accoreconsole-test.sh. Safety-gate assertions used to live here and were worthless:
# the heredoc is quoted, so $TOOLS never expanded, and $TOOLS is not exported either. The
# child therefore grepped a non-existent path, and its failure branch incremented
# GATE_VIOLATIONS inside a subshell where the parent could never observe it. Gate checks now
# live in the parent (below) and are enforced by tools/check-live-gates.py.
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

# REGRESSION: regenerating without --status-file must NOT erase recorded tests or the
# publication `redactions` provenance block. An earlier version silently dropped both while
# still printing "validation OK", which is quiet evidence loss.
python - "$TMP/ev" <<'PYEOF'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1]) / "manifest.json"
d = json.loads(p.read_text(encoding="utf-8"))
d["tests"] = [{"id": "KEEP-ME", "status": "PASS", "evidence": ["good.json"]}]
d["redactions"] = {"applied_at_utc": "2026-01-01T00:00:00Z", "artifacts": []}
p.write_text(json.dumps(d, indent=2), encoding="utf-8")
PYEOF
python "$TOOLS/make-manifest.py" "$TMP/ev" --phase X --run-id R >/dev/null 2>&1
if python - "$TMP/ev" <<'PYEOF'
import json, pathlib, sys
d = json.loads((pathlib.Path(sys.argv[1]) / "manifest.json").read_text(encoding="utf-8"))
assert any(t.get("id") == "KEEP-ME" for t in d.get("tests", [])), "tests block was erased"
assert "redactions" in d, "redactions provenance was erased"
sys.exit(0)
PYEOF
then
  echo "  PASS  regeneration preserves tests and redaction provenance"; pass=$((pass+1))
else
  echo "  FAIL  regeneration erased non-derived manifest blocks"; fail=$((fail+1))
fi

# ATOMIC WRITE: an interrupted write must leave the PREVIOUS manifest intact, never a
# truncated one. The failure is injected by making json.dumps raise, so the temp file is
# created and then abandoned -- exactly the interruption this guards against.
printf '{"phase":"ORIGINAL","tests":[]}' > "$TMP/ev/manifest.json"
if python - "$TOOLS" "$TMP/ev" <<'PYEOF'
import importlib.util, json, pathlib, sys
tools, ev = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("mm", tools / "make-manifest.py")
mm = importlib.util.module_from_spec(spec); spec.loader.exec_module(mm)
real = mm.json.dumps
def boom(*a, **k):
    raise RuntimeError("injected serialization failure")
mm.json.dumps = boom
try:
    mm.write_manifest_atomically(ev / "manifest.json", {"phase": "NEW"})
except RuntimeError:
    pass
else:
    print("  FAIL  injected failure did not propagate"); sys.exit(1)
mm.json.dumps = real
# The previous manifest must be byte-identical, and no temp file may be left behind.
if (ev / "manifest.json").read_text(encoding="utf-8") != '{"phase":"ORIGINAL","tests":[]}':
    print("  FAIL  previous manifest was damaged by a failed write"); sys.exit(1)
leftovers = list(ev.glob("manifest.json.*.tmp"))
if leftovers:
    print(f"  FAIL  temp file left behind: {leftovers}"); sys.exit(1)
sys.exit(0)
PYEOF
then
  echo "  PASS  failed manifest write preserves the previous manifest (atomic)"; pass=$((pass+1))
else
  fail=$((fail+1))
fi

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
#
# The check is delegated to tools/check-live-gates.py, which parses the AST and requires a
# REAL call to require_safety_review_passed() -- a comment or a string mentioning it does not
# satisfy it. It also verifies that dap-probe.py's gate is conditional on live intent, that
# the retired shell harness routes through safe_process.py --gate, and that no tooling reads
# an environment variable that could bypass the gate.
check "every live CAD entrypoint is gated (AST-checked)" \
  python "$TOOLS/check-live-gates.py" "$TOOLS"

# MUTATION TESTS: a gate check that cannot fail is not a check.
# These run check-live-gates.py against deliberately DAMAGED COPIES of the tools tree, so
# they prove the checker detects the exact defect class that previously slipped through
# (gate call deleted, or replaced by a comment). No live harness is ever executed here: the
# mutation only edits source text and the checker is static.
MUT_DIR="$TMP/gate-mutations"
mutation_caught() {
  local name="$1" mutator="$2"
  local d="$MUT_DIR/$(echo "$name" | tr ' /' '__')"
  rm -rf "$d"; mkdir -p "$d"; cp -r "$TOOLS" "$d/tools"
  if ! python - "$d/tools" <<PYEOF
import pathlib, sys
$mutator
PYEOF
  then
    echo "  FAIL  mutation '$name' could not be applied"
    fail=$((fail+1)); return
  fi
  if python "$TOOLS/check-live-gates.py" "$d/tools" >/dev/null 2>&1; then
    echo "  FAIL  mutation '$name' was NOT detected (gate check is ineffective)"
    fail=$((fail+1))
  else
    echo "  PASS  mutation detected: $name"
    pass=$((pass+1))
  fi
}

mutation_caught "dap-probe gate call deleted" \
  'p = pathlib.Path(sys.argv[1])/"dap-probe.py"; t = p.read_text(encoding="utf-8"); t = t.replace("sp.require_safety_review_passed(\"dap-probe.py live mode\")", "pass"); p.write_text(t, encoding="utf-8")'
mutation_caught "t01-5 harness gate replaced by comment" \
  'p = pathlib.Path(sys.argv[1])/"t01-5-definitive.py"; t = p.read_text(encoding="utf-8"); t = t.replace("    sp.require_safety_review_passed(\"t01-5-definitive.py\")", "    # sp.require_safety_review_passed(\"t01-5-definitive.py\")"); p.write_text(t, encoding="utf-8")'
mutation_caught "retired GUI harness gate removed" \
  'p = pathlib.Path(sys.argv[1])/"run-acad-gui-test.sh"; t = p.read_text(encoding="utf-8"); t = t.replace("python \"$TOOLS_DIR/safe_process.py\" --gate \"run-acad-gui-test.sh\" || exit $?", "echo retired"); p.write_text(t, encoding="utf-8")'

# Regression mutations for the two defects Sourcery found in the FIRST version of this
# checker (PR #1). Both were reproduced against that version before being fixed.
mutation_caught "gate replaced by a same-named method on an unrelated object" \
  'p = pathlib.Path(sys.argv[1])/"dap-probe.py"; t = p.read_text(encoding="utf-8"); t = t.replace("CONTENT_LENGTH = b\"Content-Length: \"", "class _Noop:\n    def require_safety_review_passed(self, *a):\n        return None\nhelper = _Noop()\n\nCONTENT_LENGTH = b\"Content-Length: \""); t = t.replace("sp.require_safety_review_passed(\"dap-probe.py live mode\")", "helper.require_safety_review_passed(\"dap-probe.py live mode\")"); p.write_text(t, encoding="utf-8")'
mutation_caught "new ungated live harness with an unlisted filename" \
  'import pathlib as _pl; (_pl.Path(sys.argv[1])/"dap-live-newprobe.py").write_text("import subprocess\nsubprocess.Popen([r\"D:/acad.exe\"])\n", encoding="utf-8")'

# Privacy guard: the local account/machine identifier must never appear in a tracked file.
# It leaked TWICE during this audit -- once in the P0/P1 artifacts, and again in the P2 build
# log produced while fixing the first leak -- so it is now a checked invariant, not a
# one-off cleanup. The identifier comes from the environment and is never written into this
# repo (doing so would re-publish the string the check exists to remove).
if [ -n "${CB_REDACT_IDENTIFIER:-}" ]; then
  check "no local machine identifier in tracked files" \
    python "$TOOLS/redact-evidence.py" --check
else
  echo "  SKIP  identifier check (CB_REDACT_IDENTIFIER not set in this environment)"
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
