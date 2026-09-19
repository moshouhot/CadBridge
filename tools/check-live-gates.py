#!/usr/bin/env python3
"""Verify that every live-CAD entrypoint is still behind the hard safety gate.

WHY THIS IS A SEPARATE PROGRAM
------------------------------
`tools/selftest.sh` used to assert this with `grep -q require_safety_review_passed`. That
check had two defects, both of which made the suite report PASS while the protection was
gone:

  1. It ran INSIDE a generated fake `accoreconsole.exe`. The fake was written with a quoted
     heredoc (`<<'EOF'`), so `$TOOLS` was never expanded, and `$TOOLS` is not exported by
     selftest.sh. The child therefore ran `grep -q ... "$TOOLS/dap-probe.py"` against the
     path `/dap-probe.py`, which does not exist. The failure branch also incremented
     `GATE_VIOLATIONS` inside a subshell, so even a real failure could not reach the
     parent's exit status. Removing the gate from dap-probe.py still produced
     "18 passed, 0 failed".

  2. Even with a correct path, a substring match is satisfied by a COMMENT. Deleting the
     call and leaving a sentence about it behind would still pass.

TWO FURTHER DEFECTS (found by Sourcery review of PR #1, both reproduced before fixing)
--------------------------------------------------------------------------------------
  3. NAME-ONLY MATCHING. Any call whose final attribute name was
     `require_safety_review_passed` counted as the gate, no matter what object it was called
     on. A harness could define its own no-op object with a method of that name and the
     checker would still report it as gated. The check now RESOLVES the receiver to the
     module actually imported from `safe_process`, so an unrelated same-named method fails.

  4. HARD-CODED FILE LIST. Only a fixed list of filenames was examined, so a NEW live
     harness with a different name was never visited and produced no failure -- the
     "every entrypoint is gated" invariant could silently regress. Entrypoints are now
     DISCOVERED from live-CAD behaviour (launching/attaching to acad.exe, accoreconsole or
     the debug adapter), and the allowlist exists only to record reviewed exceptions, never
     to define the scope of the check.

Exit codes: 0 = all gates intact, 1 = at least one gate is missing or bypassable,
2 = usage error.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys

GATE_FUNC = "require_safety_review_passed"
GATE_MODULE = "safe_process"

# ---------------------------------------------------------------------------------
# Discovery: which files are live-CAD entrypoints?
# ---------------------------------------------------------------------------------
# Detection is behaviour-based (AST + non-comment shell text), NOT filename-based, so a newly
# added harness is in scope automatically.
#
# Two signal strengths are distinguished, because "mentions acad.exe in a docstring" and
# "starts acad.exe" are very different claims:
#
#   STRONG = the file can start or drive a host: it calls the ownership launchers
#            (launch_and_record / launch_job_and_record), speaks DAP (DapClient), or invokes
#            a subprocess with a CAD executable literal as an argument.
#   WEAK   = the file only mentions a CAD executable name (docstrings, error messages,
#            detection patterns, log text).
#
# Only STRONG makes a file a live entrypoint. WEAK-only files must still be listed in
# NON_LIVE_ALLOWLIST with a reason, so an unexplained file cannot sit in the grey zone.
EXE_PAT = re.compile(r"(acad\.exe|accoreconsole(\.exe)?|AutoLispDebugAdapter)", re.I)

LIVE_LAUNCH_CALLS = {
    "launch_and_record", "launch_job_and_record", "DapClient",
}

SUBPROCESS_CALLS = {"Popen", "run", "call", "check_output", "check_call"}

# The tool that only launches the headless core engine.
SHELL_EXE_PAT = re.compile(r"(accoreconsole(\.exe)?|acad\.exe|AutoLispDebugAdapter)", re.I)

# Files with WEAK signals only: they reference CAD names but cannot start or drive a host.
# Each entry is a claim, and the checker fails if a listed file turns out to have a STRONG
# signal, so the list cannot be used to hide a real entrypoint.
NON_LIVE_ALLOWLIST: dict[str, str] = {
    "check-live-gates.py": "this checker; matches CAD names in its own detection patterns only",
    "decode-accoreconsole-output.py": "decodes captured UTF-16 output files; never executes a host",
    "pathconv.py": "pure path string conversion; mentions accoreconsole only in its docstring",
    "probe-debug-adapter.py": "reads adapter bytes off disk for string analysis; never executes it",
    "bounded_worker.py": "generic bounded subprocess worker; no CAD executable is referenced",
    "job-family-fixture.py": "launches a copy of itself as a sleep fixture; no CAD involved",
    "com_read_worker.py": "child worker that reads an already-verified instance; owns nothing",
    "make-manifest.py": "hashes evidence files; no process launch",
    "compare-security-baseline.py": "diffs two JSON baselines; no process launch",
    "inventory-autocad.ps1": "READ-ONLY inventory of installed AutoCAD; never launches a host",
    "scan-autocad.ps1": "READ-ONLY install scan; never launches a host",
    "capture-env.ps1": "captures environment metadata; never launches a host",
    "capture-cad-security-baseline.ps1": "READ-ONLY registry/profile capture; never launches a host",
    "diagnose-refpath.ps1": "READ-ONLY reference-path diagnosis; never launches a host",
    "scan-sdk-assemblies.ps1": "READ-ONLY SDK assembly scan; never launches a host",
    "selftest.sh": (
        "runs the suite. It only ever INVOKES the harness scripts (argument-validation cases) "
        "and writes a fake accoreconsole stand-in with a heredoc redirect; it never executes a "
        "real CAD binary."
    ),
    "run-compat-matrix.sh": (
        "delegator: it invokes run-accoreconsole-test.sh and never executes a CAD binary "
        "itself, so it inherits that harness's documented limits."
    ),
    "test-repl-probe-offline.py": "offline fake-based tests; no CAD is started",
    "run-acad-gui-test.sh": (
        "RETIRED harness: it no longer launches anything. Every remaining mention of acad.exe "
        "is in its comments. Its live body was replaced by a call to safe_process.py --gate, "
        "so old commands fail loudly. It is listed here because it is intentionally inert; "
        "the --gate routing is still asserted by selftest.sh's mutation test."
    ),
}

# Modules that are part of the gate/ownership mechanism itself and therefore cannot be
# expected to gate themselves. Listed explicitly so the exclusion is auditable rather than
# an implicit special case.
GATE_MECHANISM_FILES: dict[str, str] = {
    "safe_process.py": (
        "DEFINES the gate (require_safety_review_passed) and the ownership layer. Its live "
        "launch paths are the fixture/self-test paths, which substitute a fake subprocess.Popen "
        "and a fake process table, so they start nothing real. It is the mechanism under test, "
        "not a harness."
    ),
}

# Retired harnesses: their live body is gone, but they must STILL route through the gate so
# that an old command fails loudly instead of silently doing nothing. These are inert (hence
# not discovered as live), so their gate routing is asserted separately here. Dropping the
# routing must fail the check.
RETIRED_HARNESSES: dict[str, str] = {
    "run-acad-gui-test.sh": (
        "Historical full-GUI harness that rediscovered acad.exe by directory scan and could "
        "terminate a concurrently started user process. Retired because keeping a dangerous "
        "implementation behind a liftable gate would make it dangerous again. It must keep "
        "calling safe_process.py --gate so old commands fail loudly."
    ),
}

# Live entrypoints that are deliberately NOT gated, with a reason. Kept as an explicit,
# validated registry rather than an implicit filename list: a file detected as live but
# absent from both the gate check and this registry is a FAILURE, and selftest.sh mutation-
# tests an unlisted live harness to prove that.
LIVE_EXCEPTIONS: dict[str, str] = {
    "run-accoreconsole-test.sh": (
        "Drives the headless core engine (accoreconsole.exe), not a GUI session, and is the "
        "mechanism the P1 evidence was produced with. It is deliberately usable offline "
        "against a fake stand-in so the harness itself can be tested, so a hard gate would "
        "disable its own regression tests. LIMITS, stated rather than hidden: it accepts "
        "--dwg and will open that drawing, and 'headless' is not by itself proof of "
        "harmlessness. It must only be pointed at disposable fixtures outside the user's "
        "working set. Any change that makes it launch a GUI session must add a gate."
    ),
    "run-compat-matrix.sh": (
        "Driver that only delegates to run-accoreconsole-test.sh, inheriting its limits."
    ),
}


def _gate_bindings(tree: ast.AST) -> set[str]:
    """Local names bound to the safe_process module by a real import statement.

    `import safe_process as sp`            -> {"sp"}
    `import safe_process`                  -> {"safe_process"}
    `from safe_process import x as gate`   -> handled separately by name
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == GATE_MODULE:
                    names.add(a.asname or a.name)
    return names


def _gate_imported_names(tree: ast.AST) -> set[str]:
    """Names imported DIRECTLY from safe_process (e.g. `from safe_process import gate`)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == GATE_MODULE:
            for a in node.names:
                if a.name == GATE_FUNC:
                    names.add(a.asname or a.name)
    return names


def _resolved_gate_calls(tree: ast.AST) -> list[ast.Call]:
    """Calls that provably resolve to safe_process.require_safety_review_passed.

    A call counts only when its receiver is a name bound by an actual `import safe_process`
    statement, or when the function itself was imported from safe_process. A same-named
    method on an unrelated object is therefore NOT accepted.
    """
    module_names = _gate_bindings(tree)
    direct_names = _gate_imported_names(tree)
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == GATE_FUNC:
            recv = fn.value
            if isinstance(recv, ast.Name) and recv.id in module_names:
                out.append(node)
        elif isinstance(fn, ast.Name) and fn.id in direct_names:
            out.append(node)
    return out


def _guard_mentions_live_intent(tree: ast.AST, calls: list[ast.Call]) -> bool:
    """True if some resolved gate call sits inside an `if` whose condition is live intent."""
    call_lines = {c.lineno for c in calls}
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        cond = ast.unparse(node.test)
        if "live_intent" not in cond and "live" not in cond.lower():
            continue
        for stmt in node.body:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Call) and sub.lineno in call_lines:
                    return True
    return False


def _shell_invokes_gate(path: pathlib.Path) -> bool:
    """True if a NON-COMMENT line runs safe_process with --gate."""
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "safe_process.py" in line and "--gate" in line:
            return True
    return False


def _env_reads(path: pathlib.Path) -> list[str]:
    """Names of environment variables actually READ by the code (AST-based).

    Prose that merely mentions a variable is not a bypass, so a text search produces false
    positives. Only real reads count.
    """
    names: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            base = node.value
            if isinstance(base, ast.Attribute) and base.attr == "environ":
                key = node.slice
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    names.append(key.value)
        elif isinstance(node, ast.Call):
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else None)
            if fname in ("get", "getenv", "pop", "setdefault") and node.args:
                is_environ = (
                    (isinstance(fn, ast.Attribute) and fn.attr == "getenv")
                    or (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Attribute)
                        and fn.value.attr == "environ")
                )
                if is_environ:
                    a0 = node.args[0]
                    if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                        names.append(a0.value)
    return names


def _py_live_signal(path: pathlib.Path) -> tuple[bool, str]:
    """STRONG signal: can this Python file start or drive a CAD host?"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return False, ""
    launch_calls: list[str] = []
    exe_arg_spawns: list[str] = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        fn = n.func
        nm = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else None)
        if nm in LIVE_LAUNCH_CALLS:
            launch_calls.append(nm)
        if nm in SUBPROCESS_CALLS:
            for arg in list(n.args) + [k.value for k in n.keywords]:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                            and EXE_PAT.search(sub.value):
                        exe_arg_spawns.append(nm)
                        break
    if launch_calls:
        return True, "calls " + ",".join(sorted(set(launch_calls)))
    if exe_arg_spawns:
        return True, "spawns a CAD executable via " + ",".join(sorted(set(exe_arg_spawns)))
    return False, ""


def _py_mentions_cad(path: pathlib.Path) -> bool:
    """WEAK signal: does the file merely mention a CAD executable name?"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and EXE_PAT.search(n.value):
            return True
    return False


def _sh_live_signal(path: pathlib.Path) -> tuple[bool, str]:
    """Does a shell script actually invoke a CAD executable (non-comment lines only)?

    Precise on purpose: a line that merely NAMES a CAD binary (an echo, a grep pattern, a
    heredoc redirect target such as `cat > "$FAKE_ACAD/accoreconsole.exe"`, or a script called
    `run-accoreconsole-test.sh`) is not an invocation. Only two shapes count:
      * an assignment whose right-hand side is a CAD executable path
        (`ACAD_EXE="$ACAD_DIR/accoreconsole.exe"`), or
      * the first word of the command being a CAD executable.
    """
    cad_exe = re.compile(r"(acad|accoreconsole|AutoLispDebugAdapter)\.exe$", re.I)
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Assignment: VAR=<something ending in a CAD exe>
        m = re.match(r"^[A-Za-z_][A-Za-z0-9_]*=(.+)$", line)
        if m:
            rhs = m.group(1).strip().strip('"\'')
            if cad_exe.search(rhs):
                return True, "assigns a CAD executable path"
            continue
        # Direct execution: the first word is a CAD executable.
        first = line.split()[0].strip('"\'')
        if cad_exe.search(first):
            return True, "executes a CAD executable"
    return False, ""


def check(tools: pathlib.Path) -> list[str]:
    problems: list[str] = []
    discovered: list[str] = []
    exceptions_used: list[str] = []

    for f in sorted(tools.iterdir()):
        if not f.is_file():
            continue
        name = f.name
        if f.suffix == ".py":
            live, why = _py_live_signal(f)
            weak = _py_mentions_cad(f)
        elif f.suffix == ".sh":
            live, why = _sh_live_signal(f)
            weak = SHELL_EXE_PAT.search(f.read_text(encoding="utf-8", errors="replace")) is not None
        else:
            continue

        if name in GATE_MECHANISM_FILES:
            continue

        if live and name in NON_LIVE_ALLOWLIST:
            # Contradiction: the allowlist claims this file cannot start a session, but it
            # was detected doing so. Surface it instead of silently trusting the list.
            problems.append(
                f"{name}: listed as non-live ({NON_LIVE_ALLOWLIST[name]}) but detected as a "
                f"live entrypoint ({why}); the allowlist entry is wrong"
            )
            continue

        if not live:
            if weak and name not in NON_LIVE_ALLOWLIST and f.suffix in (".py", ".sh"):
                problems.append(
                    f"{name}: mentions a CAD executable but is not classified. Add it to "
                    f"NON_LIVE_ALLOWLIST with a reason, or it is an unaccounted entrypoint"
                )
            continue

        discovered.append(name)

        if name in LIVE_EXCEPTIONS:
            exceptions_used.append(name)
            continue

        if f.suffix == ".py":
            try:
                tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError as e:
                problems.append(f"{name}: cannot parse: {e}")
                continue
            calls = _resolved_gate_calls(tree)
            if not calls:
                problems.append(
                    f"{name}: live entrypoint with no call resolving to "
                    f"{GATE_MODULE}.{GATE_FUNC}() (a comment, a string, or a same-named method "
                    f"on another object does not count)"
                )
                continue
            src = f.read_text(encoding="utf-8", errors="replace")
            if "live_intent" in src and not _guard_mentions_live_intent(tree, calls):
                problems.append(
                    f"{name}: gate call is not guarded by a live-intent condition, so it cannot "
                    f"distinguish offline protocol probing from a live CAD run"
                )
        elif f.suffix == ".sh":
            if not _shell_invokes_gate(f):
                problems.append(
                    f"{name}: live entrypoint with no non-comment line invoking "
                    f"safe_process.py --gate, so it is not frozen"
                )

    # No environment-variable bypass anywhere in the Python tooling.
    for f in sorted(tools.glob("*.py")):
        for name in _env_reads(f):
            if "CBRIDGE_ACK" in name:
                problems.append(
                    f"{f.name}: reads environment variable {name!r}; an environment variable "
                    f"must never bypass the live-run gate"
                )

    # Retired harnesses must keep routing through the gate so old commands fail loudly.
    for name in sorted(RETIRED_HARNESSES):
        f = tools / name
        if not f.is_file():
            problems.append(f"{name}: retired harness is missing (expected it to stay as a guard)")
            continue
        if not _shell_invokes_gate(f):
            problems.append(
                f"{name}: retired harness no longer routes through safe_process.py --gate, so "
                f"an old command would fail silently instead of loudly"
            )

    # Stale registry entries: a listed file that no longer exists means the registry has
    # drifted away from reality.
    for name in sorted(set(NON_LIVE_ALLOWLIST) | set(LIVE_EXCEPTIONS)
                       | set(GATE_MECHANISM_FILES) | set(RETIRED_HARNESSES)):
        if not (tools / name).exists():
            problems.append(f"{name}: listed in the registry but the file no longer exists")

    if not problems:
        print(f"  PASS  all live CAD entrypoints are gated or explicitly excepted ({tools})")
        print(f"        discovered {len(discovered)} live entrypoint(s): "
              f"{', '.join(sorted(discovered))}")
        if exceptions_used:
            print(f"        documented exceptions: {', '.join(sorted(exceptions_used))}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tools_dir", nargs="?", default="tools")
    args = ap.parse_args()
    tools = pathlib.Path(args.tools_dir)
    if not tools.is_dir():
        print(f"ERROR: not a directory: {tools}", file=sys.stderr)
        return 2
    problems = check(tools)
    if problems:
        for p in problems:
            print(f"  FAIL  {p}")
        print(f"live-gate check FAILED with {len(problems)} problem(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
