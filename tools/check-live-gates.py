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

So the check is now an actual-call check performed by this program, and selftest.sh runs it
against BOTH the real tree and deliberately mutated copies (mutation tests). It is
deliberately able to take a directory argument so the mutation tests can point it at a
damaged tree.

Exit codes: 0 = all gates intact, 1 = at least one gate is missing or bypassable,
2 = usage error.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import sys

GATE_FUNC = "require_safety_review_passed"

# Harnesses that can start or drive a live CAD session and therefore must call the gate.
GATED_PY_GLOBS = ("t01-5-*.py", "dap-attach-*.py")
GATED_PY_FILES = ("dap-session.py", "dap-a14-sequence.py")

# dap-probe.py is allowed to run OFFLINE (pure adapter protocol probing). Its gate must be
# conditional on live intent, so it is checked separately from the unconditional group.
CONDITIONAL_PY = ("dap-probe.py",)

# Retired shell harness: must route through the safe_process gate instead of running.
GATED_SH_FILES = ("run-acad-gui-test.sh",)

# No environment variable may bypass the gate.
FORBIDDEN_ENV_BYPASS = "CBRIDGE_ACK"


def _called_gate_names(tree: ast.AST) -> list[ast.Call]:
    """Every real call to the gate function (AST-based, so comments cannot satisfy it)."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else None)
            if name == GATE_FUNC:
                out.append(node)
    return out


def _guard_mentions_live_intent(tree: ast.AST, calls: list[ast.Call]) -> bool:
    """True if some call sits inside an `if` whose condition references live intent.

    This is what makes dap-probe.py safe: the gate must be conditional on attach/program/
    acad.exe intent, not merely present somewhere in the file.
    """
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

    Prose that merely mentions a variable (a docstring explaining that a bypass was removed)
    is not a bypass, so a text search produces false positives. Only real reads count:
    os.environ[...], os.environ.get(...), os.getenv(...), and os.environ.pop/get-style calls.
    """
    names: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            # os.environ["NAME"]
            base = node.value
            if isinstance(base, ast.Attribute) and base.attr == "environ":
                key = node.slice
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    names.append(key.value)
        elif isinstance(node, ast.Call):
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else None)
            # os.environ.get("NAME"), os.getenv("NAME")
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


def check(tools: pathlib.Path) -> list[str]:
    problems: list[str] = []

    gated_py: list[pathlib.Path] = []
    for pattern in GATED_PY_GLOBS:
        gated_py.extend(sorted(tools.glob(pattern)))
    gated_py.extend(tools / f for f in GATED_PY_FILES)

    for f in gated_py:
        if not f.is_file():
            problems.append(f"{f.name}: expected gated harness is missing")
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as e:
            problems.append(f"{f.name}: cannot parse: {e}")
            continue
        calls = _called_gate_names(tree)
        if not calls:
            problems.append(
                f"{f.name}: no actual call to {GATE_FUNC}() -- a live harness must call the "
                f"gate (a comment or string mentioning it does not count)"
            )

    # dap-probe.py: gate must be conditional on live intent.
    for name in CONDITIONAL_PY:
        f = tools / name
        if not f.is_file():
            problems.append(f"{name}: expected harness is missing")
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as e:
            problems.append(f"{name}: cannot parse: {e}")
            continue
        calls = _called_gate_names(tree)
        if not calls:
            problems.append(f"{name}: no actual call to {GATE_FUNC}() for its live mode")
        elif not _guard_mentions_live_intent(tree, calls):
            problems.append(
                f"{name}: the gate call is not guarded by a live-intent condition, so it "
                f"cannot distinguish offline protocol probing from a live CAD run"
            )

    # Retired shell harness must route through the gate.
    for name in GATED_SH_FILES:
        f = tools / name
        if not f.is_file():
            problems.append(f"{name}: expected retired harness is missing")
            continue
        if not _shell_invokes_gate(f):
            problems.append(
                f"{name}: no non-comment line invokes safe_process.py with --gate, so this "
                f"live entrypoint is not frozen"
            )

    # No environment-variable bypass anywhere in the Python tooling. Checked as real env
    # READS so that documentation describing the removed bypass is not a false positive.
    for f in sorted(tools.glob("*.py")):
        for name in _env_reads(f):
            if FORBIDDEN_ENV_BYPASS in name:
                problems.append(
                    f"{f.name}: reads environment variable {name!r}; an environment "
                    f"variable must never bypass the live-run gate"
                )

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
    print(f"  PASS  all live CAD entrypoints are gated ({tools})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
