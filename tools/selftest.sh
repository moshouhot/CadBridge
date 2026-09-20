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
# No AutoCAD is launched. The suite includes pure tooling checks plus non-live compiled regressions.
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

echo "== Python static gates =="
# These are repository-wide gates, not tests for one known variable name.  The local
# b715501 verification found two changed harnesses with an undefined name even though this
# self-test reported 50/50 PASS.  compileall catches syntax/import-time parse failures and
# pyflakes catches undefined-name classes without executing any live harness.
check "all Python tooling compiles" env PYTHONPYCACHEPREFIX="$TMP/pycache" python -m compileall -q "$TOOLS"
check "pyflakes clean across tools/*.py" python -m pyflakes "$TOOLS"/*.py

echo "== execution baseline transaction regression =="
check "production ExecutionContextBaseline publication regression" \
  bash "$REPO/tests/run-execution-baseline-tests.sh"

echo "== pathconv =="
check "to-win converts /f/a/b"        test "$(python "$TOOLS/pathconv.py" to-win '/f/a/b')" = 'F:\a\b'
check "to-msys converts F:\\a\\b"     test "$(python "$TOOLS/pathconv.py" to-msys 'F:\a\b')" = '/f/a/b'
check "to-win-slash converts /f/a/b"  test "$(python "$TOOLS/pathconv.py" to-win-slash '/f/a/b')" = 'F:/a/b'
expect_fail "unknown mode fails"      python "$TOOLS/pathconv.py" bogus '/f/a/b'

echo "== compatibility parser =="
RUNTIME_PARSED="$(printf 'compiled_target=net8.0-windows; clr=8.0.0; runtime_framework=.NET 8.0.0; process_arch=x64\r\n' | \
  sed -e 's/.*runtime_framework=[[:space:]]*//' -e 's/[[:space:]]*;.*$//' | tr -d '\r')"
if [ "$RUNTIME_PARSED" = ".NET 8.0.0" ]; then
  echo "  PASS  runtime_framework parser returns the measured value only"; pass=$((pass+1))
else
  echo "  FAIL  runtime_framework parser returned: $RUNTIME_PARSED"; fail=$((fail+1))
fi

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
echo "outside evidence" > "$TMP/outside.txt"
cat > "$TMP/ev/traversal-status.json" <<'EOF'
{"tests":[{"id":"T3b","status":"PASS","evidence":["../outside.txt"]}]}
EOF
expect_fail "parent-traversal evidence path rejected even when target exists" python "$TOOLS/make-manifest.py" "$TMP/ev" --phase X --run-id R --status-file "$TMP/ev/traversal-status.json"
ABS_OUTSIDE="$(cd "$TMP" && pwd)/outside.txt"
printf '{"tests":[{"id":"T3c","status":"PASS","evidence":["%s"]}]}' "$ABS_OUTSIDE" > "$TMP/ev/absolute-status.json"
expect_fail "absolute evidence path rejected even when target exists" python "$TOOLS/make-manifest.py" "$TMP/ev" --phase X --run-id R --status-file "$TMP/ev/absolute-status.json"
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

# Carried redaction provenance must still describe the CURRENT stored artifact bytes.
STALE_DIR="$TMP/stale-redaction"
mkdir -p "$STALE_DIR"
printf 'original stored bytes\n' > "$STALE_DIR/evidence.txt"
python "$TOOLS/make-manifest.py" "$STALE_DIR" --phase X --run-id R >/dev/null 2>&1
python - "$STALE_DIR" <<'PYEOF'
import hashlib, json, pathlib, sys
d = pathlib.Path(sys.argv[1])
p = d / "evidence.txt"
m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
m["redactions"] = {
    "artifacts": [{
        "path": "evidence.txt",
        "stored_sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        "stored_bytes": p.stat().st_size,
    }]
}
(d / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
PYEOF
printf 'tampered after provenance\n' > "$STALE_DIR/evidence.txt"
if python "$TOOLS/make-manifest.py" "$STALE_DIR" --phase X --run-id R >/dev/null 2>&1; then
  echo "  FAIL  stale carried redaction hash was accepted"; fail=$((fail+1))
else
  if python - "$STALE_DIR/manifest.json" <<'PYEOF'
import json, pathlib, sys
m = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert m["validation"]["ok"] is False
assert any("stored_sha256" in x and "does not match" in x
           for x in m["validation"]["problems"])
PYEOF
  then
    echo "  PASS  stale carried redaction hash makes manifest validation fail"; pass=$((pass+1))
  else
    echo "  FAIL  stale redaction was rejected without truthful manifest validation"; fail=$((fail+1))
  fi
fi

# ATOMIC WRITE: an interrupted write must leave the PREVIOUS manifest intact, never a
# truncated one.
#
# The failure is injected at os.replace -- the ACTUAL commit step, AFTER the temp file has
# been written and fsync'd. An earlier version injected the failure in json.dumps, which
# happens BEFORE mkstemp, so it never exercised the write, the replace, or the temp-file
# cleanup at all: it could not have caught a broken cleanup path. Injecting at replace does.
#
# Three things are asserted: the failure propagates (not swallowed), the previous manifest is
# BYTE-identical (not merely parseable), and no temp file is left behind.
ORIGINAL='{"phase":"ORIGINAL","tests":[]}'
printf '%s' "$ORIGINAL" > "$TMP/ev/manifest.json"
if python - "$TOOLS" "$TMP/ev" "$ORIGINAL" <<'PYEOF'
import importlib.util, os, pathlib, sys
tools, ev, original = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
spec = importlib.util.spec_from_file_location("mm", tools / "make-manifest.py")
mm = importlib.util.module_from_spec(spec); spec.loader.exec_module(mm)

# Record whether the temp file was actually created before the injected failure.
real_replace = os.replace
real_mkstemp = mm.tempfile.mkstemp
state = {"temp_created": False, "replace_attempted": False}

def tracking_mkstemp(*a, **k):
    fd, name = real_mkstemp(*a, **k)
    state["temp_created"] = True
    state["temp_name"] = name
    return fd, name

def failing_replace(src, dst):
    # This is the real commit step. Fail here, after the temp file exists.
    state["replace_attempted"] = True
    raise OSError("injected replace failure")

mm.tempfile.mkstemp = tracking_mkstemp
mm.os.replace = failing_replace
try:
    mm.write_manifest_atomically(ev / "manifest.json", {"phase": "NEW", "tests": [1, 2, 3]})
except OSError:
    pass
else:
    print("  FAIL  injected os.replace failure did not propagate"); sys.exit(1)
finally:
    mm.tempfile.mkstemp = real_mkstemp
    mm.os.replace = real_replace

if not state["temp_created"]:
    print("  FAIL  test is invalid: the temp file was never created, so nothing was tested")
    sys.exit(1)
if not state["replace_attempted"]:
    print("  FAIL  test is invalid: os.replace was never reached")
    sys.exit(1)

# The previous manifest must be BYTE-identical.
after = (ev / "manifest.json").read_bytes()
if after != original.encode("utf-8"):
    print(f"  FAIL  previous manifest was modified by a failed write: {after!r}")
    sys.exit(1)

# No temp file may survive, including the one this test saw created.
leftovers = sorted(p.name for p in ev.glob("manifest.json.*.tmp"))
if leftovers:
    print(f"  FAIL  temp file(s) left behind after failure: {leftovers}")
    sys.exit(1)
if not pathlib.Path(state["temp_name"]).exists() is False:
    print("  FAIL  the tracked temp file still exists")
    sys.exit(1)
sys.exit(0)
PYEOF
then
  echo "  PASS  failed os.replace preserves the previous manifest byte-for-byte (atomic)"; pass=$((pass+1))
else
  fail=$((fail+1))
fi

# A cleanup failure during a failed write must be REPORTED, not swallowed. Silently discarding
# it would leave manifest.json.*.tmp files behind while the function still claims the "no temp
# file on failure" invariant, and repeated failures would accumulate stale temp files.
if python - "$TOOLS" "$TMP" <<'PYEOF'
import contextlib, importlib.util, io, pathlib, shutil, sys
tools, tmp = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("mm", tools / "make-manifest.py")
mm = importlib.util.module_from_spec(spec); spec.loader.exec_module(mm)
d = tmp / "cleanupfail"; shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
(d / "manifest.json").write_text('{"phase":"OLD"}', encoding="utf-8")
mm.os.replace = lambda a, b: (_ for _ in ()).throw(OSError("replace failed"))
mm.os.unlink = lambda p: (_ for _ in ()).throw(OSError("unlink failed"))
captured = io.StringIO()
try:
    with contextlib.redirect_stderr(captured):
        mm.write_manifest_atomically(d / "manifest.json", {"phase": "NEW"})
except OSError:
    pass
else:
    print("  FAIL  injected failure did not propagate"); sys.exit(1)
if "could not remove temporary file" not in captured.getvalue():
    print("  FAIL  a failed cleanup was swallowed instead of reported"); sys.exit(1)
if (d / "manifest.json").read_text(encoding="utf-8") != '{"phase":"OLD"}':
    print("  FAIL  previous manifest was modified"); sys.exit(1)
sys.exit(0)
PYEOF
then
  echo "  PASS  a failed temp-file cleanup is reported, not swallowed"; pass=$((pass+1))
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
check "every live CAD entrypoint is gated (repo-scope AST check)" \
  python "$TOOLS/check-live-gates.py" "$REPO"

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
  'p = pathlib.Path(sys.argv[1])/"dap-probe.py"; t = p.read_text(encoding="utf-8"); t = t.replace("sp.require_safety_review_passed(\"dap-probe.py\")", "pass"); p.write_text(t, encoding="utf-8")'
mutation_caught "t01-5 harness gate replaced by comment" \
  'p = pathlib.Path(sys.argv[1])/"t01-5-definitive.py"; t = p.read_text(encoding="utf-8"); t = t.replace("    sp.require_safety_review_passed(\"t01-5-definitive.py\")", "    # sp.require_safety_review_passed(\"t01-5-definitive.py\")"); p.write_text(t, encoding="utf-8")'
mutation_caught "retired GUI harness gate removed" \
  'p = pathlib.Path(sys.argv[1])/"run-acad-gui-test.sh"; t = p.read_text(encoding="utf-8"); t = t.replace("python \"$TOOLS_DIR/safe_process.py\" --gate \"run-acad-gui-test.sh\" || exit $?", "echo retired"); p.write_text(t, encoding="utf-8")'
mutation_caught "retired GUI harness only echoes the gate command" \
  'p = pathlib.Path(sys.argv[1])/"run-acad-gui-test.sh"; t = p.read_text(encoding="utf-8"); t = t.replace("python \"$TOOLS_DIR/safe_process.py\" --gate \"run-acad-gui-test.sh\" || exit $?", "echo python \"$TOOLS_DIR/safe_process.py\" --gate \"run-acad-gui-test.sh\""); p.write_text(t, encoding="utf-8")'

# Regression mutations for the two defects Sourcery found in the FIRST version of this
# checker (PR #1). Both were reproduced against that version before being fixed.
mutation_caught "gate replaced by a same-named method on an unrelated object" \
  'p = pathlib.Path(sys.argv[1])/"dap-probe.py"; t = p.read_text(encoding="utf-8"); t = t.replace("CONTENT_LENGTH = b\"Content-Length: \"", "class _Noop:\n    def require_safety_review_passed(self, *a):\n        return None\nhelper = _Noop()\n\nCONTENT_LENGTH = b\"Content-Length: \""); t = t.replace("sp.require_safety_review_passed(\"dap-probe.py\")", "helper.require_safety_review_passed(\"dap-probe.py\")"); p.write_text(t, encoding="utf-8")'
mutation_caught "new ungated live harness with an unlisted filename" \
  'import pathlib as _pl; (_pl.Path(sys.argv[1])/"dap-live-newprobe.py").write_text("import subprocess\nsubprocess.Popen([r\"D:/acad.exe\"])\n", encoding="utf-8")'

# Regressions for the two checker gaps found while fixing the Sourcery findings. Both were
# reproduced against the previous checker before being fixed.
mutation_caught "gate import alias reassigned to an unrelated object" \
  'p = pathlib.Path(sys.argv[1])/"dap-probe.py"; t = p.read_text(encoding="utf-8"); t = t.replace("CONTENT_LENGTH = b\"Content-Length: \"", "class _Noop:\n    def require_safety_review_passed(self, *a):\n        return None\nsp = _Noop()\n\nCONTENT_LENGTH = b\"Content-Length: \""); p.write_text(t, encoding="utf-8")'
mutation_caught "gate import shadowed by a function parameter" \
  'p = pathlib.Path(sys.argv[1])/"dap-probe.py"; t = p.read_text(encoding="utf-8"); t = t.replace("def main(", "def _shadow(sp):\n    return sp\n\ndef main(", 1); p.write_text(t, encoding="utf-8")'
# Coverage mutation: a live path built from a COMPUTED executable name is invisible to
# behavioural discovery, so only the classification-manifest requirement can catch it.
mutation_caught "new live harness with a computed exe path (invisible to discovery)" \
  'import pathlib as _pl; (_pl.Path(sys.argv[1])/"com-attach-harness.py").write_text("import os\nimport win32com.client\n\ndef main():\n    exe = os.environ[\"CADDIR\"] + chr(92) + \"acad\" + \".exe\"\n    app = win32com.client.GetActiveObject(\"AutoCAD.Application\")\n    return 0\n", encoding="utf-8")'
mutation_caught "PowerShell script that really starts a CAD host" \
  'import pathlib as _pl; (_pl.Path(sys.argv[1])/"launch-cad.ps1").write_text("Start-Process -FilePath chr(34)+" + chr(39) + "acad.exe" + chr(39) + "\n", encoding="utf-8")'
mutation_caught "classified PowerShell inventory script directly invokes acad.exe" \
  'p = pathlib.Path(sys.argv[1])/"inventory-autocad.ps1"; t = p.read_text(encoding="utf-8"); t += "\nacad.exe /nologo\n"; p.write_text(t, encoding="utf-8")'

REPO_SCOPE_MUT="$MUT_DIR/repository-scope"
rm -rf "$REPO_SCOPE_MUT"; mkdir -p "$REPO_SCOPE_MUT/tools" "$REPO_SCOPE_MUT/tests"
cp -r "$TOOLS"/. "$REPO_SCOPE_MUT/tools/"
cp "$REPO/tests/run-radius-policy-tests.sh" "$REPO_SCOPE_MUT/tests/"
cp "$REPO/tests/run-execution-baseline-tests.sh" "$REPO_SCOPE_MUT/tests/"
cat > "$REPO_SCOPE_MUT/tests/new-live-harness.sh" <<'EOF'
#!/usr/bin/env bash
acad.exe
EOF
if python "$TOOLS/check-live-gates.py" "$REPO_SCOPE_MUT" >/dev/null 2>&1; then
  echo "  FAIL  repo-scope checker missed an unclassified live script under tests/"
  fail=$((fail+1))
else
  echo "  PASS  repo-scope checker detects live scripts outside tools/"
  pass=$((pass+1))
fi

HEREDOC_FIXTURE="$TMP/heredoc-nonlive.sh"
cat > "$HEREDOC_FIXTURE" <<'OUTER_EOF'
#!/usr/bin/env bash
cat > /tmp/generated-live-fixture <<'INNER_EOF'
acad.exe
INNER_EOF
echo offline
OUTER_EOF
if python - "$TOOLS/check-live-gates.py" "$HEREDOC_FIXTURE" <<'PYEOF'
import importlib.util, pathlib, sys
tool, fixture = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("gate_checker_under_test", tool)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
live, why = mod._sh_live_signal(fixture)
raise SystemExit(1 if live else 0)
PYEOF
then
  echo "  PASS  shell live detection ignores heredoc fixture payload"
  pass=$((pass+1))
else
  echo "  FAIL  heredoc fixture payload was misclassified as executable shell"
  fail=$((fail+1))
fi

# Regressions for the six defects Sourcery found in the SECOND full review of this PR. Every
# one was reproduced against the checker as it stood before being fixed.
mutation_caught "gate function imported directly then reassigned" \
  'p = pathlib.Path(sys.argv[1])/"dap-probe.py"; t = p.read_text(encoding="utf-8"); t = t.replace("import safe_process as sp", "import safe_process as sp\nfrom safe_process import require_safety_review_passed as gate"); t = t.replace("        sp.require_safety_review_passed(\"dap-probe.py\")", "        gate = lambda *a, **k: None\n        gate(\"dap-probe.py\")"); p.write_text(t, encoding="utf-8")'
mutation_caught "live-intent guard polarity inverted" \
  'p = pathlib.Path(sys.argv[1])/"dap-probe.py"; t = p.read_text(encoding="utf-8"); t = t.replace("    if live_intent:", "    if not live_intent:"); p.write_text(t, encoding="utf-8")'
mutation_caught "live-intent comparison inverted" \
  'p = pathlib.Path(sys.argv[1])/"dap-probe.py"; t = p.read_text(encoding="utf-8"); t = t.replace("    if live_intent:", "    if live_intent == False:"); p.write_text(t, encoding="utf-8")'
mutation_caught "compound live-intent guard made unreachable" \
  'p = pathlib.Path(sys.argv[1])/"dap-probe.py"; t = p.read_text(encoding="utf-8"); t = t.replace("    if live_intent:", "    if live_intent and False:"); p.write_text(t, encoding="utf-8")'
mutation_caught "gate borrows another harness allowlist key" \
  'p = pathlib.Path(sys.argv[1])/"dap-session.py"; t = p.read_text(encoding="utf-8"); t = t.replace("sp.require_safety_review_passed(\"dap-session.py\")", "sp.require_safety_review_passed(\"t01-5-definitive.py\")"); p.write_text(t, encoding="utf-8")'
mutation_caught "gate hidden in statically dead if False branch" \
  'p = pathlib.Path(sys.argv[1])/"dap-session.py"; t = p.read_text(encoding="utf-8"); t = t.replace("    sp.require_safety_review_passed(\"dap-session.py\")", "    if False:\n        sp.require_safety_review_passed(\"dap-session.py\")"); p.write_text(t, encoding="utf-8")'
mutation_caught "gate moved into an uncalled helper" \
  'p = pathlib.Path(sys.argv[1])/"dap-session.py"; t = p.read_text(encoding="utf-8"); t = t.replace("    sp.require_safety_review_passed(\"dap-session.py\")", "    pass"); t += "\n\ndef _unused_gate_for_mutation():\n    sp.require_safety_review_passed(\"dap-session.py\")\n"; p.write_text(t, encoding="utf-8")'
mutation_caught "module-level gate moved after __main__ entrypoint" \
  'p = pathlib.Path(sys.argv[1])/"dap-session.py"; t = p.read_text(encoding="utf-8"); t = t.replace("    sp.require_safety_review_passed(\"dap-session.py\")", "    pass"); t += "\nsp.require_safety_review_passed(\"dap-session.py\")\n"; p.write_text(t, encoding="utf-8")'
mutation_caught "gate moved after the first live DapClient sink" \
  'p = pathlib.Path(sys.argv[1])/"dap-session.py"; t = p.read_text(encoding="utf-8"); gate="    sp.require_safety_review_passed(\"dap-session.py\")\n"; t=t.replace(gate, ""); sink="    c = dap.DapClient([args.adapter, \"--\", args.product], args.transcript, timeout=args.timeout)\n"; t=t.replace(sink, sink+gate); p.write_text(t, encoding="utf-8")'
mutation_caught "conditional live sink inserted before gate in same branch" \
  'p = pathlib.Path(sys.argv[1])/"dap-probe.py"; t = p.read_text(encoding="utf-8"); gate="        sp.require_safety_review_passed(\"dap-probe.py\")\n"; injected="        DapClient([args.adapter], args.transcript, timeout=args.timeout)\n"+gate; t=t.replace(gate, injected); p.write_text(t, encoding="utf-8")'
mutation_caught "conditional gate nested under optional stack branch" \
  'p = pathlib.Path(sys.argv[1])/"dap-probe.py"; t = p.read_text(encoding="utf-8"); gate="        sp.require_safety_review_passed(\"dap-probe.py\")"; t=t.replace(gate, "        if args.stack:\n            sp.require_safety_review_passed(\"dap-probe.py\")"); p.write_text(t, encoding="utf-8")'
mutation_caught "standalone COM worker gate removed" \
  'p = pathlib.Path(sys.argv[1])/"com_read_worker.py"; t = p.read_text(encoding="utf-8"); t = t.replace("    sp.require_safety_review_passed(\"com_read_worker.py\")\n\n", ""); p.write_text(t, encoding="utf-8")'
mutation_caught "nested script reusing a registry basename" \
  'import pathlib as _pl; d = _pl.Path(sys.argv[1])/"subdir"; d.mkdir(exist_ok=True); (d/"safe_process.py").write_text("import subprocess\nsubprocess.Popen([chr(39)+chr(97)+chr(99)+chr(97)+chr(100)+chr(46)+chr(101)+chr(120)+chr(101)+chr(39)])\n", encoding="utf-8")'
mutation_caught "extensionless executable with a shebang" \
  'import pathlib as _pl; (_pl.Path(sys.argv[1])/"cad-launcher").write_text("#!/usr/bin/env python3\nimport subprocess\nsubprocess.Popen([chr(39)+chr(97)+chr(99)+chr(97)+chr(100)+chr(46)+chr(101)+chr(120)+chr(101)+chr(39)])\n", encoding="utf-8")'
mutation_caught "environment read through an aliased environ mapping" \
  'p = pathlib.Path(sys.argv[1])/"dap-probe.py"; t = p.read_text(encoding="utf-8"); t = t.replace("import safe_process as sp", "import os as _os\nimport safe_process as sp\nenv = _os.environ"); t = t.replace("        sp.require_safety_review_passed(\"dap-probe.py\")", "        if env.get(\"CBRIDGE_ACK_UNREVIEWED_LIVE\"):\n            pass\n        sp.require_safety_review_passed(\"dap-probe.py\")"); p.write_text(t, encoding="utf-8")'

# Regressions for the two defects Sourcery found in the third review round, both in
# make-manifest.py. Reproduced against the previous version before fixing.
#
# (a) A manifest that exists but cannot be parsed was treated as {} and overwritten, which
#     discards the non-derived tests/redactions blocks -- re-creating the very data-loss
#     defect the preservation logic exists to prevent.
mkdir -p "$TMP/corrupt"
printf '{ this is not valid json' > "$TMP/corrupt/manifest.json"
printf '{"ok":true}' > "$TMP/corrupt/a.json"
CORRUPT_BEFORE=$(sha256sum "$TMP/corrupt/manifest.json" | cut -d' ' -f1)
if python "$TOOLS/make-manifest.py" "$TMP/corrupt" --phase X --run-id R >/dev/null 2>&1; then
  echo "  FAIL  a corrupt existing manifest was overwritten instead of refused"
  fail=$((fail+1))
else
  CORRUPT_AFTER=$(sha256sum "$TMP/corrupt/manifest.json" | cut -d' ' -f1)
  if [ "$CORRUPT_BEFORE" = "$CORRUPT_AFTER" ]; then
    echo "  PASS  corrupt manifest refused and left byte-identical"; pass=$((pass+1))
  else
    echo "  FAIL  corrupt manifest was modified"; fail=$((fail+1))
  fi
fi

# (b) artifact_count must be checked against the artifacts array length. Because the tool now
#     DERIVES the field, a fresh generation is trivially consistent -- so the test must exercise
#     the path where a contradicting value can still enter: `--extra`. That is the real risk
#     (an injected or hand-written block disagreeing with the array it describes).
mkdir -p "$TMP/countchk"
printf '{"ok":true}' > "$TMP/countchk/a.json"
printf '{"artifact_count": 999}' > "$TMP/countchk/extra.json"
python "$TOOLS/make-manifest.py" "$TMP/countchk" --phase X --run-id R \
  --extra "$TMP/countchk/extra.json" >/dev/null 2>&1
COUNT_RC=$?
if [ "$COUNT_RC" -eq 0 ]; then
  echo "  FAIL  a contradicting artifact_count injected via --extra was accepted"
  fail=$((fail+1))
else
  # It must be REJECTED, and the written file must still be self-consistent.
  if python - "$TMP/countchk" <<'PYEOF'
import json, pathlib, sys
d = json.loads((pathlib.Path(sys.argv[1]) / "manifest.json").read_text(encoding="utf-8"))
assert d["artifact_count"] == len(d["artifacts"]), "written manifest is internally inconsistent"
assert d["validation"]["ok"] is False, "inconsistency was not reflected in validation.ok"
sys.exit(0)
PYEOF
  then
    echo "  PASS  contradicting artifact_count is rejected and the manifest stays consistent"
    pass=$((pass+1))
  else
    fail=$((fail+1))
  fi
fi

# (b2) --extra is metadata only. It must not replace derived hashes or validation state.
mkdir -p "$TMP/extrachk"
printf '{"real":true}' > "$TMP/extrachk/real.json"
cat > "$TMP/extrachk/evil-extra.json" <<'EOF'
{"artifacts":[{"path":"fake.bin","bytes":1,"sha256":"deadbeef"}],"validation":{"ok":true,"problems":[]},"redactions":{"forged":true}}
EOF
if python "$TOOLS/make-manifest.py" "$TMP/extrachk" --phase X --run-id R \
  --extra "$TMP/extrachk/evil-extra.json" >/dev/null 2>&1; then
  echo "  FAIL  --extra was allowed to replace protected artifact/validation fields"
  fail=$((fail+1))
else
  if python - "$TMP/extrachk" <<'PYEOF'
import json, pathlib, sys
d = json.loads((pathlib.Path(sys.argv[1]) / "manifest.json").read_text(encoding="utf-8"))
paths = {a["path"] for a in d["artifacts"]}
assert "real.json" in paths, paths
assert "fake.bin" not in paths, paths
assert "redactions" not in d, d.get("redactions")
assert d["validation"]["ok"] is False
assert any("protected manifest field" in p for p in d["validation"]["problems"])
PYEOF
  then
    echo "  PASS  --extra cannot override derived artifacts or validation"; pass=$((pass+1))
  else
    echo "  FAIL  protected fields were corrupted by --extra"; fail=$((fail+1))
  fi
fi

# (b3) A syntactically valid but non-object --extra must make validation.ok false.
mkdir -p "$TMP/extra-array"
printf '{"ok":true}' > "$TMP/extra-array/a.json"
printf '["not-an-object"]' > "$TMP/extra-array/extra.json"
if python "$TOOLS/make-manifest.py" "$TMP/extra-array" --phase X --run-id R \
  --extra "$TMP/extra-array/extra.json" >/dev/null 2>&1; then
  echo "  FAIL  non-object --extra unexpectedly exited 0"
  fail=$((fail+1))
else
  if python - "$TMP/extra-array/manifest.json" <<'PYEOF'
import json, pathlib, sys
d = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert d["validation"]["ok"] is False
assert any("--extra must contain a JSON object" in x for x in d["validation"]["problems"])
PYEOF
  then
    echo "  PASS  non-object --extra is reflected in validation.ok=false"; pass=$((pass+1))
  else
    echo "  FAIL  non-object --extra wrote contradictory validation state"; fail=$((fail+1))
  fi
fi

# (b4) Atomic replacement must preserve an existing manifest's permission mode on POSIX.
if python - "$TMP" "$TOOLS/make-manifest.py" <<'PYEOF'
import os, pathlib, stat, subprocess, sys
root, tool = pathlib.Path(sys.argv[1]) / "mode-preserve", pathlib.Path(sys.argv[2])
root.mkdir(parents=True, exist_ok=True)
(root / "a.json").write_text('{"ok":true}', encoding="utf-8")
subprocess.check_call([sys.executable, str(tool), str(root), "--phase", "X", "--run-id", "R"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if os.name == "nt":
    sys.exit(0)
manifest = root / "manifest.json"
os.chmod(manifest, 0o644)
subprocess.check_call([sys.executable, str(tool), str(root), "--phase", "X", "--run-id", "R"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
assert stat.S_IMODE(manifest.stat().st_mode) == 0o644
PYEOF
then
  echo "  PASS  manifest atomic replace preserves existing permissions"
  pass=$((pass+1))
else
  echo "  FAIL  manifest atomic replace changed existing permissions"
  fail=$((fail+1))
fi

# (b5) A new manifest should use normal creation permissions rather than mkstemp private mode.
if python - "$TMP" "$TOOLS/make-manifest.py" <<'PYEOF'
import os, pathlib, stat, subprocess, sys
if os.name == "nt":
    raise SystemExit(0)
root, tool = pathlib.Path(sys.argv[1]) / "fresh-mode", pathlib.Path(sys.argv[2])
root.mkdir(parents=True, exist_ok=True)
(root / "a.json").write_text('{"ok":true}', encoding="utf-8")
old = os.umask(0o022)
try:
    subprocess.check_call(
        [sys.executable, str(tool), str(root), "--phase", "X", "--run-id", "R"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
finally:
    os.umask(old)
assert stat.S_IMODE((root / "manifest.json").stat().st_mode) == 0o644
PYEOF
then
  echo "  PASS  fresh manifest respects umask (022 -> 0644)"; pass=$((pass+1))
else
  echo "  FAIL  fresh manifest inherited mkstemp private mode"; fail=$((fail+1))
fi

# (c) Line endings must be preserved, or every manifest edit becomes an unreviewable
#     whole-file diff (docs/evidence is -text in .gitattributes, so bytes are what matter).
mkdir -p "$TMP/crlf"
printf '{\r\n  "phase": "X",\r\n  "artifacts": [],\r\n  "tests": []\r\n}\r\n' > "$TMP/crlf/manifest.json"
printf '{"ok":true}' > "$TMP/crlf/a.json"
python "$TOOLS/make-manifest.py" "$TMP/crlf" --phase X --run-id R >/dev/null 2>&1
if python - "$TMP/crlf" <<'PYEOF'
import pathlib, sys
b = (pathlib.Path(sys.argv[1]) / "manifest.json").read_bytes()
crlf = b.count(b"\r\n"); bare = b.count(b"\n") - crlf
sys.exit(0 if crlf > bare else 1)
PYEOF
then
  echo "  PASS  manifest line endings are preserved (CRLF stays CRLF)"; pass=$((pass+1))
else
  echo "  FAIL  manifest line endings were rewritten"; fail=$((fail+1))
fi

# Privacy guard: the local account/machine identifier must never appear in a tracked file.
# It leaked THREE times during this audit -- the P0/P1 artifacts, the P1 .raw evidence files,
# and the P2 build log produced while fixing the first leak -- so it is now a checked
# invariant, not a one-off cleanup. The identifier comes from the environment and is never
# written into this repo (doing so would re-publish the string the check exists to remove).
if [ -n "${CB_REDACT_IDENTIFIER:-}" ]; then
  check "no local machine identifier in tracked files" \
    python "$TOOLS/redact-evidence.py" --check
  expect_fail "explicit nonexistent redaction path is refused" \
    python "$TOOLS/redact-evidence.py" --check "$TMP/definitely-missing-redaction-input.txt"

  check "tracked-file redaction is anchored to the CadBridge repository" \
    bash -c "cd '$TMP' && CB_REDACT_IDENTIFIER='$CB_REDACT_IDENTIFIER' python '$TOOLS/redact-evidence.py' --check"

  FAILMODE_DIR="$TMP/redaction-failmode"
  mkdir -p "$FAILMODE_DIR"
  python - "$FAILMODE_DIR" <<'PYEOF'
import pathlib, sys
pathlib.Path(sys.argv[1], "opaque.dat").write_bytes(bytes(range(256)) * 4)
PYEOF
  expect_fail "scrub mode fails when an intended target is unprocessable" \
    python "$TOOLS/redact-evidence.py" "$FAILMODE_DIR"
  expect_fail "dry-run fails when an intended target is unprocessable" \
    python "$TOOLS/redact-evidence.py" --dry-run "$FAILMODE_DIR"

  # Encoding coverage: a UTF-8-only scan reports a false clean on the UTF-16 and GB18030
  # evidence this repository actually contains (accoreconsole writes UTF-16LE when stdout is
  # not a console; the host console is a Chinese Windows install). BOM-less UTF-16 cannot be
  # found by trial decoding, so each encoding is asserted separately against a fixture.
  ENC_DIR="$TMP/encodings"
  mkdir -p "$ENC_DIR"
  python - "$ENC_DIR" "$CB_REDACT_IDENTIFIER" <<'PYEOF'
import pathlib, sys
d, ident = pathlib.Path(sys.argv[1]), sys.argv[2]
line = "p: " + chr(67) + ":" + chr(92) + "Users" + chr(92) + ident + chr(92) + "x" + chr(10)
for enc, name in (("utf-16-le", "le-bomless.txt"), ("utf-16-be", "be-bomless.txt"),
                  ("utf-16", "bom.txt"), ("utf-8", "utf8.txt"), ("gb18030", "gbk.txt")):
    (d / name).write_bytes(line.encode(enc))
heavy = ("中文证据内容" * 120) + line + ("更多中文内容" * 120)
(d / "le-chinese-heavy.txt").write_bytes(heavy.encode("utf-16-le"))
(d / "be-chinese-heavy.txt").write_bytes(heavy.encode("utf-16-be"))
PYEOF
  for encfile in "$ENC_DIR"/*.txt; do
    if CB_REDACT_IDENTIFIER="$CB_REDACT_IDENTIFIER" python "$TOOLS/redact-evidence.py" \
         --check "$encfile" >/dev/null 2>&1; then
      echo "  FAIL  identifier not detected in $(basename "$encfile")"
      fail=$((fail+1))
    else
      echo "  PASS  identifier detected in $(basename "$encfile")"
      pass=$((pass+1))
    fi
  done

  MALFORMED_HEAVY="$ENC_DIR/le-chinese-heavy-malformed.txt"
  python - "$MALFORMED_HEAVY" "$CB_REDACT_IDENTIFIER" <<'PYEOF'
import pathlib, sys
p, ident = pathlib.Path(sys.argv[1]), sys.argv[2]
line = ("中文证据内容" * 120) + ident + ("更多中文内容" * 120)
p.write_bytes(line.encode("utf-16-le") + b"\xff")
PYEOF
  expect_fail "malformed Chinese-heavy UTF-16 cannot fall through to GB18030" \
    python "$TOOLS/redact-evidence.py" --check "$MALFORMED_HEAVY"

  ALIGN_DIR="$TMP/utf16-alignment"
  mkdir -p "$ALIGN_DIR"
  python - "$ALIGN_DIR" "$CB_REDACT_IDENTIFIER" <<'PYEOF'
import pathlib, sys
d, ident = pathlib.Path(sys.argv[1]), sys.argv[2]
prefix = "".join(chr((ord(ch) << 8) | 0x41) for ch in ident)
text = prefix + "|" + ident + "|tail"
(d / "odd-offset.txt").write_bytes(text.encode("utf-16-le"))
PYEOF
  CB_REDACT_IDENTIFIER="$CB_REDACT_IDENTIFIER" python "$TOOLS/redact-evidence.py" \
    "$ALIGN_DIR/odd-offset.txt" >/dev/null 2>&1
  if python - "$ALIGN_DIR" "$CB_REDACT_IDENTIFIER" <<'PYEOF'
import pathlib, sys
d, ident = pathlib.Path(sys.argv[1]), sys.argv[2]
text = (d / "odd-offset.txt").read_bytes().decode("utf-16-le")
prefix = "".join(chr((ord(ch) << 8) | 0x41) for ch in ident)
assert text.startswith(prefix + "|"), "unrelated odd-offset bytes were modified"
assert ident not in text, "real decoded identifier was not removed"
assert "<REDACTED-USER>" in text
PYEOF
  then
    echo "  PASS  UTF-16 scrub replaces only aligned decoded identifier spans"; pass=$((pass+1))
  else
    echo "  FAIL  UTF-16 scrub altered an odd-offset byte coincidence"; fail=$((fail+1))
  fi

  NAME_DIR="$TMP/redaction-name-leak"
  mkdir -p "$NAME_DIR"
  NAME_FILE="$NAME_DIR/$CB_REDACT_IDENTIFIER.txt"
  printf '%s\n' "$CB_REDACT_IDENTIFIER" > "$NAME_FILE"
  expect_fail "redaction refuses a target filename containing the private identifier" \
    python "$TOOLS/redact-evidence.py" "$NAME_FILE"
  if [ ! -e "$NAME_FILE.redaction.txt" ]; then
    echo "  PASS  rejected private filename produced no provenance sidecar"; pass=$((pass+1))
  else
    echo "  FAIL  rejected private filename was repeated into provenance"; fail=$((fail+1))
  fi

  CASE_FILE="$NAME_DIR/case-variant.txt"
  printf '%s\n' "$(printf '%s' "$CB_REDACT_IDENTIFIER" | tr '[:lower:]' '[:upper:]')" > "$CASE_FILE"
  expect_fail "case-insensitive identifier variant cannot be reported clean" \
    python "$TOOLS/redact-evidence.py" --check "$CASE_FILE"

  PATH_ROOT="$TMP/redaction-path-component"
  mkdir -p "$PATH_ROOT/$CB_REDACT_IDENTIFIER/nested"
  printf 'clean contents\n' > "$PATH_ROOT/$CB_REDACT_IDENTIFIER/nested/log.txt"
  expect_fail "repository-relative directory component containing identifier is refused" \
    python "$TOOLS/redact-evidence.py" --check "$PATH_ROOT"

  if python - "$TOOLS/redact-evidence.py" "$TMP" "$CB_REDACT_IDENTIFIER" <<'PYEOF'
import importlib.util, pathlib, sys
tool, tmp, ident = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
spec = importlib.util.spec_from_file_location("redact_symlink_test", tool)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
p = tmp / "symlink-fixture.txt"
p.write_text(ident, encoding="utf-8")
real = mod.pathlib.Path.is_symlink
mod.pathlib.Path.is_symlink = lambda self: self == p
try:
    try:
        mod.scrub_file(p, ident, dry_run=False)
    except RuntimeError as e:
        assert "symlink" in str(e).lower()
    else:
        raise AssertionError("symlink target was accepted")
finally:
    mod.pathlib.Path.is_symlink = real
PYEOF
  then
    echo "  PASS  symlink targets are refused before evidence replacement"; pass=$((pass+1))
  else
    echo "  FAIL  symlink target refusal regression failed"; fail=$((fail+1))
  fi

  # An undecodable tracked file must NOT be reported as a clean result: that would be a false
  # clean for a file that was never scanned. Reproduced against the previous version, which
  # printed PASS while warning that the file was skipped.
  BIN_DIR="$TMP/undecodable"
  mkdir -p "$BIN_DIR"
  python - "$BIN_DIR" <<'PYEOF'
import pathlib, sys
d = pathlib.Path(sys.argv[1])
d.joinpath("opaque.bin").write_bytes(bytes(range(256)) * 4)
PYEOF
  if CB_REDACT_IDENTIFIER="$CB_REDACT_IDENTIFIER" python "$TOOLS/redact-evidence.py" \
       --check "$BIN_DIR" >/dev/null 2>&1; then
    echo "  FAIL  an unscannable file was reported as a clean result (false clean)"
    fail=$((fail+1))
  else
    echo "  PASS  an unscannable file fails --check instead of reporting a false clean"
    pass=$((pass+1))
  fi

  # A scrub must change ONLY the identifier bytes. An earlier version decoded with
  # errors="replace" and encoded back the same way, which rewrote every malformed sequence in
  # the file: a lone surrogate in a UTF-16 evidence file became U+FFFD. Reproduced before
  # fixing (tail bytes 00 d8 came back as fd ff).
  MIX_DIR="$TMP/malformed-utf16"
  mkdir -p "$MIX_DIR"
  python - "$MIX_DIR" "$CB_REDACT_IDENTIFIER" <<'PYEOF'
import pathlib, sys
d, ident = pathlib.Path(sys.argv[1]), sys.argv[2]
bs = chr(92)
raw = ("path C:" + bs + "Users" + bs + ident + bs + "x").encode("utf-16-le") + b"\x00\xd8"
(d / "mixed.txt").write_bytes(raw)
PYEOF
  if CB_REDACT_IDENTIFIER="$CB_REDACT_IDENTIFIER" python "$TOOLS/redact-evidence.py" \
       --check "$MIX_DIR" >/dev/null 2>&1; then
    echo "  FAIL  a file with malformed sequences was reported clean instead of refused"
    fail=$((fail+1))
  else
    echo "  PASS  malformed-but-identified UTF-16 is refused rather than silently rewritten"
    pass=$((pass+1))
  fi

  # The byte-level scrub must preserve every non-identifier byte. Asserted directly: after
  # scrubbing a valid UTF-16 file, the surrounding text is unchanged and only the identifier
  # is gone.
  OK_DIR="$TMP/utf16-scrub"
  mkdir -p "$OK_DIR"
  python - "$OK_DIR" "$CB_REDACT_IDENTIFIER" <<'PYEOF'
import pathlib, sys
d, ident = pathlib.Path(sys.argv[1]), sys.argv[2]
bs = chr(92)
(d / "ok.txt").write_bytes(("keep C:" + bs + "Users" + bs + ident + bs + "x end").encode("utf-16-le"))
PYEOF
  CB_REDACT_IDENTIFIER="$CB_REDACT_IDENTIFIER" python "$TOOLS/redact-evidence.py" \
    "$OK_DIR/ok.txt" >/dev/null 2>&1
  if python - "$OK_DIR" "$CB_REDACT_IDENTIFIER" <<'PYEOF'
import pathlib, sys
d, ident = pathlib.Path(sys.argv[1]), sys.argv[2]
text = (d / "ok.txt").read_bytes().decode("utf-16-le")
assert ident not in text, "identifier still present"
assert text.startswith("keep C:"), f"leading text changed: {text!r}"
assert text.endswith("x end"), f"trailing text changed: {text!r}"
sys.exit(0)
PYEOF
  then
    echo "  PASS  byte-level scrub removes only the identifier and preserves the rest"
    pass=$((pass+1))
  else
    fail=$((fail+1))
  fi

  if python - "$OK_DIR" <<'PYEOF'
import os, pathlib, stat, sys
d = pathlib.Path(sys.argv[1])
if os.name == "nt":
    raise SystemExit(0)
evidence = d / "ok.txt"
sidecar = d / "ok.txt.redaction.txt"
assert stat.S_IMODE(sidecar.stat().st_mode) == stat.S_IMODE(evidence.stat().st_mode)
PYEOF
  then
    echo "  PASS  new provenance sidecar inherits evidence permissions"; pass=$((pass+1))
  else
    echo "  FAIL  new provenance sidecar permissions are too restrictive"; fail=$((fail+1))
  fi

  TX_DIR="$TMP/redaction-transaction"
  mkdir -p "$TX_DIR"
  if python - "$TOOLS/redact-evidence.py" "$TX_DIR" "$CB_REDACT_IDENTIFIER" <<'PYEOF'
import importlib.util, pathlib, sys
tool, d, ident = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
spec = importlib.util.spec_from_file_location("redact_evidence_under_test", tool)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
p = d / "evidence.txt"
original = ("C:" + chr(92) + "Users" + chr(92) + ident + chr(92) + "evidence").encode("utf-8")
p.write_bytes(original)
real_stage = mod._stage_bytes
def injected(dest, data, mode):
    if str(dest).endswith(".redaction.txt"):
        raise OSError("injected sidecar staging failure")
    return real_stage(dest, data, mode)
mod._stage_bytes = injected
try:
    mod.scrub_file(p, ident, dry_run=False)
except RuntimeError:
    pass
else:
    raise AssertionError("injected sidecar failure was not reported")
assert p.read_bytes() == original, "evidence changed before provenance was safely staged"
assert not p.with_name(p.name + ".redaction.txt").exists(), "partial provenance was published"
PYEOF
  then
    echo "  PASS  provenance failure leaves evidence byte-identical"
    pass=$((pass+1))
  else
    echo "  FAIL  provenance failure changed evidence or left partial state"
    fail=$((fail+1))
  fi
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
