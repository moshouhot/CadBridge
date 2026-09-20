#!/usr/bin/env python3
"""Verify that every executable script is explicitly classified and that live CAD
entrypoints are behind the hard safety gate.

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

FURTHER DEFECTS, EACH REPRODUCED BEFORE FIXING
----------------------------------------------
  3. NAME-ONLY MATCHING (found by Sourcery review). Any call whose final attribute name was
     `require_safety_review_passed` counted as the gate, whatever object it was called on.
     Fixed by resolving the call to the module actually imported from `safe_process`.

  4. HARD-CODED FILE LIST (found by Sourcery review). Only fixed filenames were examined, so
     a NEW live harness was never visited and produced no failure.

  5. SCOPE-INSENSITIVE BINDING (found while fixing 4). Even after 3, a file could import
     `safe_process as sp` and then REBIND `sp` to an unrelated object before the "gate"
     call, or shadow it with a parameter. Collecting import statements without checking for
     reassignment accepts a call that never reaches safe_process. Bindings are now analysed
     for reassignment and for function-parameter shadowing, and an ambiguous binding is
     REJECTED rather than trusted.

  6. DISCOVERY IS NOT COVERAGE (found while fixing 4). Behavioural discovery cannot see a
     live path built from a computed executable name, or a COM-only attach that never
     mentions a CAD string. Discovery alone therefore cannot establish the invariant.

    The structural answer to 4 and 6 is an EXPLICIT CLASSIFICATION MANIFEST: every
    executable script in the tree (any depth, including PowerShell) must be classified, and
    an unclassified file FAILS the check. Behavioural discovery is kept as a second,
    independent signal: a file classified as non-live that is then detected as live also
    FAILS. Two independent methods must agree.

WHAT THIS DOES NOT PROVE (read before citing it)
------------------------------------------------
This is a STATIC check. It proves that the reviewed source declares a gate call on the
recognised paths. It does NOT prove runtime authorization, it cannot see through arbitrary
dynamic construction, and it is not a sandbox. The real protection remains the gate in
safe_process.py plus the process-ownership layer. Do not describe this checker as proving
that a live CAD session is safe.

Exit codes: 0 = every script classified and every live entrypoint gated,
1 = a gap or contradiction was found, 2 = usage error.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import shlex
import sys

GATE_FUNC = "require_safety_review_passed"
GATE_MODULE = "safe_process"

# ---------------------------------------------------------------------------------
# Coverage: every executable script must appear in exactly one registry below.
# ---------------------------------------------------------------------------------
SCRIPT_SUFFIXES = {".py", ".sh", ".ps1", ".cmd", ".bat", ".bash"}

# Classifications. A file MUST be in exactly one of these maps, or the check fails.

# (a) Live entrypoints that must call the gate.
#     "conditional" = may also run offline, so the gate must be guarded by live intent.
GATED: dict[str, str] = {
    "t01-5-definitive.py": "unconditional",
    "t01-5-pause-live-query.py": "unconditional",
    "t01-5-pause-query-probe.py": "unconditional",
    "t01-5-plugin-pause-read.py": "unconditional",
    "t01-5-repl-plugin-read.py": "unconditional",
    "t01-5-sync-read-forms.py": "unconditional",
    "dap-attach-a14.py": "unconditional",
    "dap-attach-matrix.py": "unconditional",
    "dap-attach-ordering.py": "unconditional",
    "dap-session.py": "unconditional",
    "dap-a14-sequence.py": "unconditional",
    "live-job-ownership-smoke.py": "unconditional",
    "dap-probe.py": "conditional",  # may probe an adapter offline; gate only on live intent
    "com_read_worker.py": "unconditional",
}

# (b) Live entrypoints deliberately NOT gated, with the reason and the limits.
LIVE_EXCEPTIONS: dict[str, str] = {
    "run-accoreconsole-test.sh": (
        "Drives the headless core engine (accoreconsole.exe), not a GUI session, and produced "
        "the P1 evidence. It is deliberately usable offline against a fake stand-in so its own "
        "argument validation can be regression-tested; a hard gate would disable those tests. "
        "LIMITS, stated not hidden: it accepts --dwg and will open that drawing, and 'headless' "
        "is not by itself proof of harmlessness. It must only be pointed at disposable fixtures. "
        "Any change that makes it launch a GUI session must add a gate."
    ),
    "run-compat-matrix.sh": (
        "Driver that only delegates to run-accoreconsole-test.sh, inheriting its limits."
    ),
}

# (c) The gate/ownership mechanism itself: it DEFINES the gate and cannot gate itself.
GATE_MECHANISM: dict[str, str] = {
    "safe_process.py": (
        "Defines require_safety_review_passed and the ownership layer. Its live launch paths "
        "are the fixture/self-test paths, which substitute a fake subprocess.Popen and a fake "
        "process table, so they start nothing real. This is the mechanism under test."
    ),
}

# (d) Retired harnesses: the live body is gone but the gate routing must remain so old
#     commands fail loudly instead of silently doing nothing.
RETIRED_HARNESSES: dict[str, str] = {
    "run-acad-gui-test.sh": (
        "Historical full-GUI harness that rediscovered acad.exe by directory scan and could "
        "terminate a concurrently started user process. Retired because keeping a dangerous "
        "implementation behind a liftable gate would make it dangerous again. Must keep calling "
        "safe_process.py --gate."
    ),
}

# (e) Verified non-live: cannot start or drive a CAD host. Each entry is a claim that the
#     checker re-tests via behavioural discovery; a contradiction fails the check.
NON_LIVE: dict[str, str] = {
    "check-live-gates.py": "this checker; matches CAD names only inside its own detection patterns",
    "decode-accoreconsole-output.py": "decodes captured UTF-16 output files; never executes a host",
    "pathconv.py": "pure path string conversion; mentions accoreconsole only in its docstring",
    "probe-debug-adapter.py": "reads adapter bytes off disk for string analysis; never executes it",
    "redact-evidence.py": "text scrubbing over tracked files; no process launch",
    "bounded_worker.py": "generic bounded subprocess worker; no CAD executable is referenced",
    "job-family-fixture.py": "launches a copy of itself as a sleep fixture; no CAD involved",
    "make-manifest.py": "hashes evidence files; no process launch",
    "compare-security-baseline.py": "diffs two JSON baselines; no process launch",
    "test-repl-probe-offline.py": "offline fake-based tests; no CAD is started",
    "selftest.sh": (
        "runs the suite. It only INVOKES the harness scripts (argument-validation cases) and "
        "writes a fake accoreconsole stand-in with a heredoc redirect; it never executes a real "
        "CAD binary."
    ),
    "tests/run-radius-policy-tests.sh": (
        "builds and runs host-independent prompt-policy logic; it does not launch AutoCAD"
    ),
    "tests/run-execution-baseline-tests.sh": (
        "builds and runs the non-live execution-baseline regression with Autodesk boundary stubs"
    ),
    "inventory-autocad.ps1": "READ-ONLY inventory of installed AutoCAD; never launches a host",
    "scan-autocad.ps1": "READ-ONLY install scan; never launches a host",
    "capture-env.ps1": "captures environment metadata; never launches a host",
    "capture-cad-security-baseline.ps1": "READ-ONLY registry/profile capture; never launches a host",
    "diagnose-refpath.ps1": "READ-ONLY reference-path diagnosis; never launches a host",
    "scan-sdk-assemblies.ps1": "READ-ONLY SDK assembly scan; never launches a host",
}

# ---------------------------------------------------------------------------------
# Behavioural discovery (second, independent signal).
# ---------------------------------------------------------------------------------
EXE_PAT = re.compile(r"(acad\.exe|accoreconsole(\.exe)?|AutoLispDebugAdapter)", re.I)
LIVE_LAUNCH_CALLS = {"launch_and_record", "launch_job_and_record", "DapClient"}
COM_LIVE_CALLS = {
    "GetActiveObject", "GetObject", "Dispatch", "DispatchEx", "CreateObject",
    "com_attach_existing",
}
SUBPROCESS_CALLS = {"Popen", "run", "call", "check_output", "check_call"}


def _all_registries() -> dict[str, str]:
    merged: dict[str, str] = {}
    for name, why in (list(GATED.items()) + list(LIVE_EXCEPTIONS.items())
                      + list(GATE_MECHANISM.items()) + list(RETIRED_HARNESSES.items())
                      + list(NON_LIVE.items())):
        merged[name] = why
    return merged


def _rebindings(tree: ast.AST, names: set[str]) -> dict[str, list[str]]:
    """Every way `names` is rebound or shadowed anywhere in the file.

    A name bound by an import is only safe to trust if nothing later rebinds it. This is the
    single analysis used for BOTH the module alias (`import safe_process as sp`) and the
    directly imported gate function (`from safe_process import require_safety_review_passed
    as gate`). Applying it to only one of them was a real defect: a harness could import the
    gate function directly, reassign it to a no-op lambda, and still be reported as gated.
    """
    rebinds: dict[str, list[str]] = {}

    def note(name: str, why: str) -> None:
        rebinds.setdefault(name, []).append(why)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                for sub in ast.walk(tgt):
                    if isinstance(sub, ast.Name) and sub.id in names:
                        note(sub.id, f"assigned at line {node.lineno}")
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name) and sub.id in names:
                    note(sub.id, f"rebound at line {node.lineno}")
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name) and sub.id in names:
                    note(sub.id, f"for-target at line {node.lineno}")
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars:
                    for sub in ast.walk(item.optional_vars):
                        if isinstance(sub, ast.Name) and sub.id in names:
                            note(sub.id, f"with-target at line {node.lineno}")
        elif isinstance(node, ast.Delete):
            for tgt in node.targets:
                for sub in ast.walk(tgt):
                    if isinstance(sub, ast.Name) and sub.id in names:
                        note(sub.id, f"deleted at line {node.lineno}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            label = getattr(node, "name", "lambda")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
                note(node.name, f"redefined by function {node.name}() at line {node.lineno}")
            a = node.args
            params = list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)
            if a.vararg:
                params.append(a.vararg)
            if a.kwarg:
                params.append(a.kwarg)
            for prm in params:
                if prm.arg in names:
                    note(prm.arg, f"shadowed by parameter of {label}() at line {node.lineno}")
        elif isinstance(node, ast.ClassDef):
            if node.name in names:
                note(node.name, f"redefined by class {node.name} at line {node.lineno}")
        elif isinstance(node, ast.NamedExpr):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name) and sub.id in names:
                    note(sub.id, f"assignment-expression target at line {node.lineno}")
        elif isinstance(node, ast.ExceptHandler):
            if isinstance(node.name, str) and node.name in names:
                note(node.name, f"exception target at line {node.lineno}")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for n in node.names:
                if n in names:
                    note(n, f"declared {type(node).__name__} at line {node.lineno}")
        elif isinstance(node, ast.ImportFrom):
            # A later re-import from a different module rebinds the name.
            for a in node.names:
                local = a.asname or a.name
                if local in names and node.module != GATE_MODULE:
                    note(local, f"re-imported from {node.module!r} at line {node.lineno}")
    return rebinds


def _gate_binding_is_safe(tree: ast.AST) -> tuple[set[str], list[str]]:
    """Names safely bound to safe_process, and reasons a binding was rejected."""
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == GATE_MODULE:
                    imported.add(a.asname or a.name)

    if not imported:
        return set(), []

    rebinds = _rebindings(tree, imported)
    reasons = [f"binding '{n}' from `import {GATE_MODULE}` is not safe: " + "; ".join(sorted(set(v)))
               for n, v in sorted(rebinds.items())]
    return imported - set(rebinds), reasons


def _direct_gate_imports(tree: ast.AST) -> tuple[set[str], list[str]]:
    """Names imported directly from safe_process, with the SAME rebinding analysis.

    `from safe_process import require_safety_review_passed as gate` binds the gate function
    itself, so a later `gate = lambda *a: None` defeats the gate. Trusting the import without
    checking for reassignment was a real defect; this now reuses _rebindings.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == GATE_MODULE:
            for a in node.names:
                if a.name == GATE_FUNC:
                    names.add(a.asname or a.name)
    if not names:
        return set(), []
    rebinds = _rebindings(tree, names)
    reasons = [f"direct import '{n}' of {GATE_FUNC} is not safe: " + "; ".join(sorted(set(v)))
               for n, v in sorted(rebinds.items())]
    return names - set(rebinds), reasons


def _resolved_gate_calls(tree: ast.AST) -> tuple[list[ast.Call], list[str]]:
    """Calls that provably resolve to safe_process.require_safety_review_passed."""
    safe_names, unsafe_reasons = _gate_binding_is_safe(tree)
    direct, direct_reasons = _direct_gate_imports(tree)
    out: list[ast.Call] = []
    suspicious: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == GATE_FUNC:
            recv = fn.value
            if isinstance(recv, ast.Name):
                if recv.id in safe_names:
                    out.append(node)
                else:
                    suspicious.append(
                        f"line {node.lineno}: calls {recv.id}.{GATE_FUNC}() but '{recv.id}' is "
                        f"not a safely-bound {GATE_MODULE} import"
                    )
        elif isinstance(fn, ast.Name) and fn.id in direct:
            out.append(node)
    return out, unsafe_reasons + direct_reasons + suspicious


def _gate_call_matches_harness(call: ast.Call, expected: str) -> bool:
    """The runtime allowlist is keyed by exact harness names; static review must be too."""
    if not call.args:
        return False
    first = call.args[0]
    return isinstance(first, ast.Constant) and isinstance(first.value, str) \
        and first.value == expected


def _is_main_guard(test: ast.AST) -> bool:
    """True only for the conventional `if __name__ == "__main__":` guard."""
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    left, right = test.left, test.comparators[0]
    return (
        isinstance(left, ast.Name) and left.id == "__name__"
        and isinstance(right, ast.Constant) and right.value == "__main__"
    ) or (
        isinstance(right, ast.Name) and right.id == "__name__"
        and isinstance(left, ast.Constant) and left.value == "__main__"
    )


def _main_is_executable_entrypoint(tree: ast.AST) -> bool:
    """Require an actual module-level __main__ guard that invokes main()."""
    for stmt in getattr(tree, "body", []):
        if not isinstance(stmt, ast.If) or not _is_main_guard(stmt.test):
            continue
        for body_stmt in stmt.body:
            for node in ast.walk(body_stmt):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                        and node.func.id == "main":
                    return True
    return False


def _contains_node(stmts: list[ast.stmt], target: ast.AST) -> bool:
    return any(target is node for stmt in stmts for node in ast.walk(stmt))


def _gate_call_on_entry_path(tree: ast.AST, call: ast.Call) -> tuple[bool, str]:
    """Conservatively prove a resolved gate call lies on the executable main path.

    A prior checker accepted any resolved gate call anywhere in the AST. That lets an
    uncalled helper or `if False:` block satisfy the checker while the real live path remains
    ungated. Current live Python harnesses all use a conventional main() entrypoint; fail
    closed if a future harness uses a shape this static proof cannot establish.
    """
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    # Reject statically dead branches containing the gate.
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Constant):
            truthy = bool(node.test.value)
            if not truthy and _contains_node(node.body, call):
                return False, f"line {call.lineno}: gate is inside a statically false if-body"
            if truthy and _contains_node(node.orelse, call):
                return False, f"line {call.lineno}: gate is inside a statically unreachable else"
        if isinstance(node, ast.While) and isinstance(node.test, ast.Constant) \
                and not bool(node.test.value) and _contains_node(node.body, call):
            return False, f"line {call.lineno}: gate is inside a statically false while-body"

    cur: ast.AST | None = call
    enclosing: ast.AST | None = None
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            enclosing = cur
            break

    if enclosing is None:
        # A module-level gate is only useful if it is a DIRECT statement that runs before
        # the conventional __main__ guard invokes main(). A gate appended after
        # `if __name__ == "__main__": main()` is too late: live work has already happened.
        top: ast.AST = call
        while top in parents and parents[top] is not tree:
            top = parents[top]
        if not (isinstance(top, ast.Expr) and top.value is call):
            return False, (
                f"line {call.lineno}: module-level gate is nested in another expression/"
                "statement and is not an unconditional top-level barrier"
            )
        for stmt in getattr(tree, "body", []):
            if isinstance(stmt, ast.If) and _is_main_guard(stmt.test):
                if getattr(top, "lineno", 10**9) >= getattr(stmt, "lineno", -1):
                    return False, (
                        f"line {call.lineno}: module-level gate executes after the __main__ "
                        "entrypoint and therefore cannot authorize main() in time"
                    )
                break
        return True, ""
    if not isinstance(enclosing, (ast.FunctionDef, ast.AsyncFunctionDef)) \
            or enclosing.name != "main":
        name = getattr(enclosing, "name", "lambda")
        return False, (
            f"line {call.lineno}: gate is inside {name!r}, not directly in executable main(); "
            "an uncalled helper cannot satisfy the live gate"
        )
    if not _main_is_executable_entrypoint(tree):
        return False, "main() contains the gate but is not invoked from a __main__ entrypoint"
    return True, ""


def _reachable_gate_calls(tree: ast.AST, calls: list[ast.Call]) -> tuple[list[ast.Call], list[str]]:
    reachable: list[ast.Call] = []
    rejected: list[str] = []
    for call in calls:
        ok, why = _gate_call_on_entry_path(tree, call)
        if ok:
            reachable.append(call)
        else:
            rejected.append(why)
    return reachable, rejected


def _call_terminal_name(call: ast.Call) -> str | None:
    fn = call.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return None


def _call_is_known_live_sink(call: ast.Call) -> bool:
    """Recognise calls that can start/attach/drive a live CAD-side process."""
    name = _call_terminal_name(call)
    if name in LIVE_LAUNCH_CALLS or name in COM_LIVE_CALLS:
        return True
    if name in SUBPROCESS_CALLS:
        for arg in list(call.args) + [kw.value for kw in call.keywords]:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                        and EXE_PAT.search(sub.value):
                    return True
    return False


def _main_function(tree: ast.AST) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for stmt in getattr(tree, "body", []):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == "main":
            return stmt
    return None


def _top_level_stmt_in_function(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, target: ast.AST
) -> ast.stmt | None:
    for stmt in fn.body:
        if stmt is target or any(node is target for node in ast.walk(stmt)):
            return stmt
    return None


def _gate_precedes_live_sinks(
    tree: ast.AST, calls: list[ast.Call], *, conditional: bool
) -> tuple[bool, str]:
    """Require the ACTUAL gate call to execute before every known live sink.

    Mapping both calls to their enclosing top-level If was too coarse: a live sink and a gate
    inside the same `if live_intent:` body both inherited the If's line, so a sink placed
    immediately before the gate still passed. After _guard_mentions_live_intent proves the
    branch polarity, lexical call ordering is the conservative proof used here.
    """
    fn = _main_function(tree)
    if fn is None:
        return False, "no module-level main() function"

    sinks = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Call) and _call_is_known_live_sink(node)
    ]
    if not sinks:
        return True, ""

    relevant_calls: list[ast.Call] = []
    for call in calls:
        stmt = _top_level_stmt_in_function(fn, call)
        if stmt is None:
            continue
        if not conditional and not (isinstance(stmt, ast.Expr) and stmt.value is call):
            continue
        relevant_calls.append(call)

    if not relevant_calls:
        kind = "live-intent gate" if conditional else "direct top-level gate statement"
        return False, f"no {kind} in main() can dominate the live sinks"

    earliest_sink = min(getattr(s, "lineno", 10**9) for s in sinks)
    earliest_gate = min(getattr(g, "lineno", 10**9) for g in relevant_calls)
    if earliest_gate < earliest_sink:
        return True, ""
    return False, (
        f"gate call line {earliest_gate} is at/after the first known live sink on line "
        f"{earliest_sink}; authorization must precede adapter/process/COM activity"
    )


def _is_live_intent_atom(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Name) and node.id == "live_intent"
    ) or (
        isinstance(node, ast.Attribute) and node.attr == "live_intent"
    )


def _live_condition_polarity(test: ast.AST) -> bool | None:
    """Prove whether a simple condition is true or false when live_intent is true.

    Unknown/compound expressions fail closed. In particular, `live_intent and False`,
    `live_intent or other_flag`, and arbitrary comparisons are NOT guessed safe.
    """
    if _is_live_intent_atom(test):
        return True
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) \
            and _is_live_intent_atom(test.operand):
        return False
    if isinstance(test, ast.Compare) and len(test.ops) == 1 \
            and len(test.comparators) == 1 and _is_live_intent_atom(test.left):
        rhs = test.comparators[0]
        if not (isinstance(rhs, ast.Constant) and isinstance(rhs.value, bool)):
            return None
        op = test.ops[0]
        if isinstance(op, (ast.Eq, ast.Is)):
            return bool(rhs.value)
        if isinstance(op, (ast.NotEq, ast.IsNot)):
            return not bool(rhs.value)
    return None


def _guard_mentions_live_intent(tree: ast.AST, calls: list[ast.Call]) -> tuple[bool, str]:
    """Prove the gate directly dominates the branch taken for live intent.

    Merely appearing somewhere under a live-intent branch is not enough. If the gate is
    nested under another optional condition, some live executions can skip authorization.
    """
    call_ids = {id(c) for c in calls}

    def direct_gate(body: list[ast.stmt]) -> ast.Call | None:
        for stmt in body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) \
                    and id(stmt.value) in call_ids:
                return stmt.value
        return None

    seen_candidate = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue

        names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(node.test) if isinstance(n, ast.Attribute)}
        if "live_intent" not in names and "live_intent" not in attrs:
            continue

        contains_any = any(
            isinstance(sub, ast.Call) and id(sub) in call_ids
            for stmt in node.body + node.orelse
            for sub in ast.walk(stmt)
        )
        if not contains_any:
            continue

        seen_candidate = True
        polarity = _live_condition_polarity(node.test)
        if polarity is None:
            return False, (
                f"line {node.lineno}: live-intent guard is compound/ambiguous; static review "
                "cannot prove the gate executes for every live run"
            )

        main_fn = _main_function(tree)
        if main_fn is None or node not in main_fn.body:
            return False, (
                f"line {node.lineno}: live-intent guard is nested under another control-flow "
                "statement; authorization is not proven to dominate every live path"
            )

        if polarity is True:
            if direct_gate(node.body) is not None:
                return True, ""
            return False, (
                f"line {node.lineno}: gate is nested inside the positive live-intent branch; "
                "it must be a direct branch statement to dominate every live path"
            )

        if direct_gate(node.orelse) is not None:
            return True, ""
        return False, (
            f"line {node.lineno}: the live path is the else branch, but no direct gate "
            "statement dominates that branch"
        )

    if seen_candidate:
        return False, "no direct gate statement dominates the proven live-intent branch"
    return False, "no gate call is reachable on a provably-positive live-intent path"


def _shell_invokes_gate(path: pathlib.Path, expected: str | None = None) -> bool:
    """Accept only an executable Python invocation of safe_process.py --gate."""
    for raw in _shell_code_lines(path):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            tokens = shlex.split(line, posix=True)
        except ValueError:
            continue
        if len(tokens) < 3:
            continue

        cmd = pathlib.PurePosixPath(tokens[0].replace("\\", "/")).name.lower()
        if cmd not in {"python", "python3", "python.exe", "python3.exe", "py", "py.exe"}:
            continue

        script = tokens[1].replace("\\", "/")
        if pathlib.PurePosixPath(script).name != "safe_process.py":
            continue
        if tokens[2] != "--gate":
            continue
        if expected is not None and (len(tokens) < 4 or tokens[3] != expected):
            continue
        return True
    return False


def _env_reads(path: pathlib.Path) -> list[str]:
    """Environment variables actually READ, including via aliases and direct imports.

    Recognising only `os.environ[...]` and a couple of attribute forms was a real defect: a
    harness could do `from os import environ` or `env = os.environ` and then read
    `environ.get("CBRIDGE_ACK_...")` / `env[...]` without the checker noticing.
    """
    names: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return names

    # Aliases that resolve to the process environment mapping.
    env_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            for a in node.names:
                if a.name == "environ":
                    env_aliases.add(a.asname or a.name)
        elif isinstance(node, ast.Assign):
            # `env = os.environ` / `env = os.environ.copy()`
            src = node.value
            if isinstance(src, ast.Attribute) and src.attr == "environ":
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        env_aliases.add(tgt.id)
            elif isinstance(src, ast.Call) and isinstance(src.func, ast.Attribute) \
                    and src.func.attr in ("copy", "copy_env") \
                    and isinstance(src.func.value, ast.Attribute) \
                    and src.func.value.attr == "environ":
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        env_aliases.add(tgt.id)

    def is_env_mapping(node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute) and node.attr == "environ":
            return True
        if isinstance(node, ast.Name) and node.id in env_aliases:
            return True
        # os.environ.copy()
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "copy" and is_env_mapping(node.func.value):
            return True
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            if is_env_mapping(node.value):
                key = node.slice
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    names.append(key.value)
        elif isinstance(node, ast.Call):
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else None)
            if fname not in ("get", "getenv", "pop", "setdefault") or not node.args:
                continue
            is_env = (
                (fname == "getenv" and isinstance(fn, ast.Attribute))
                or (isinstance(fn, ast.Attribute) and is_env_mapping(fn.value))
                or (isinstance(fn, ast.Name) and fn.id in env_aliases)
            )
            if is_env:
                a0 = node.args[0]
                if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                    names.append(a0.value)

    # Broad safety net for the case where the variable name is built dynamically (f-strings,
    # concatenation, a constant table). Only expressions that FEED an environment access are
    # considered, so documentation that merely describes the removed bypass is not flagged --
    # an earlier version scanned every string literal and produced false positives on
    # docstrings.
    env_call_sources: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and is_env_mapping(node.value):
            env_call_sources.append(node.slice)
        elif isinstance(node, ast.Call):
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else None)
            if fname in ("get", "getenv", "pop", "setdefault") and node.args:
                is_env = (
                    (fname == "getenv" and isinstance(fn, ast.Attribute))
                    or (isinstance(fn, ast.Attribute) and is_env_mapping(fn.value))
                    or (isinstance(fn, ast.Name) and fn.id in env_aliases)
                )
                if is_env:
                    env_call_sources.append(node.args[0])
    for src in env_call_sources:
        for sub in ast.walk(src):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                    and "CBRIDGE_ACK" in sub.value:
                names.append(sub.value)
    return names


def _py_live_signal(path: pathlib.Path) -> tuple[bool, str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return False, ""
    launch, spawns = [], []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        fn = n.func
        nm = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else None)
        if nm in LIVE_LAUNCH_CALLS or nm in COM_LIVE_CALLS:
            launch.append(nm)
        if nm in SUBPROCESS_CALLS:
            for arg in list(n.args) + [k.value for k in n.keywords]:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                            and EXE_PAT.search(sub.value):
                        spawns.append(nm)
                        break
    if launch:
        return True, "calls " + ",".join(sorted(set(launch)))
    if spawns:
        return True, "spawns a CAD executable via " + ",".join(sorted(set(spawns)))
    return False, ""


def _shell_code_lines(path: pathlib.Path):
    """Yield shell source, skipping only heredocs used as literal data fixtures.

    A blanket "ignore every heredoc" rule would be unsafe: payload passed to `python <<EOF`,
    `bash <<EOF`, etc. is executable code. The false positive found by Codex came from
    `cat > fixture <<EOF`, whose body is only data written to a temporary file. Therefore
    only heredocs on known data-sink commands (cat/tee) are suppressed; every other heredoc
    remains conservatively visible to behavioural discovery.
    """
    terminator: str | None = None
    strip_tabs = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if terminator is not None:
            candidate = raw.lstrip("\t") if strip_tabs else raw
            if candidate == terminator:
                terminator = None
                strip_tabs = False
            continue

        stripped = raw.strip()
        m = re.search(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2", raw)
        if m:
            # Only literal data-writer heredocs are excluded from source scanning.
            first = stripped.split()[0] if stripped.split() else ""
            if first in {"cat", "tee"}:
                terminator = m.group(3)
                strip_tabs = bool(m.group(1))
        yield raw


def _sh_live_signal(path: pathlib.Path) -> tuple[bool, str]:
    cad_exe = re.compile(r"(acad|accoreconsole|AutoLispDebugAdapter)\.exe$", re.I)
    for raw in _shell_code_lines(path):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^[A-Za-z_][A-Za-z0-9_]*=(.+)$", line)
        if m:
            if cad_exe.search(m.group(1).strip().strip("\"'")):
                return True, "assigns a CAD executable path"
            continue
        first = line.split()[0].strip("\"'") if line.split() else ""
        if cad_exe.search(first):
            return True, "executes a CAD executable"
    return False, ""


def _ps_live_signal(path: pathlib.Path) -> tuple[bool, str]:
    """PowerShell: detect actual CAD host launches, including direct PATH invocation."""
    cad_direct = re.compile(
        r"(?:^|[\\/])(acad|accoreconsole|AutoLispDebugAdapter)\.exe$",
        re.I,
    )
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if re.search(
            r"\b(Start-Process|saps|Invoke-Item)\b[^;]*"
            r"\b(acad|accoreconsole|AutoLispDebugAdapter)(\.exe)?\b",
            line,
            re.I,
        ):
            return True, "starts a CAD executable"

        m = re.match(r"^&\s+[\"']?([^\"'\s]+)", line)
        if m and re.search(
            r"(acad|accoreconsole|AutoLispDebugAdapter)\.exe$",
            m.group(1),
            re.I,
        ):
            return True, "call operator on a CAD executable"

        first = line.split()[0] if line.split() else ""
        if cad_direct.search(first):
            return True, "directly invokes a CAD executable"

        if re.search(r"New-Object\s+-ComObject\s+['\"]?AutoCAD", line, re.I):
            return True, "creates an AutoCAD COM object"
    return False, ""


def _is_executable_script(p: pathlib.Path) -> tuple[bool, str]:
    """Is this file an executable script, by suffix OR by shebang?

    Suffix-only detection missed an extensionless executable with a shebang, which is a real
    bypass: adding `tools/cad-launcher` (a shebang script) produced no unclassified-script
    error and no gate check. Shebang-bearing files are therefore in scope regardless of name.
    """
    if p.suffix.lower() in SCRIPT_SUFFIXES:
        return True, "suffix"
    try:
        with p.open("rb") as fh:
            head = fh.read(256)
    except OSError:
        return False, ""
    if head.startswith(b"#!"):
        return True, "shebang"
    return False, ""


def _classification_key(relative_path: str) -> str:
    """Map repository-relative tool paths onto the historical tools/ registry keys."""
    if relative_path.startswith("tools/"):
        return relative_path[len("tools/"):]
    return relative_path


def _out_of_scope_path(relative_path: str) -> bool:
    """Generated/captured trees are evidence, not executable first-party entrypoints."""
    if relative_path.startswith("docs/evidence/"):
        return True
    parts = pathlib.PurePosixPath(relative_path).parts
    return any(p in {".git", "bin", "obj", ".venv", "node_modules", "__pycache__"} for p in parts)


def check(root: pathlib.Path) -> list[str]:
    problems: list[str] = []
    registries = _all_registries()

    # Backward-compatible tools-only mode remains useful for isolated mutation fixtures.
    tools_only = root.name == "tools" and not (root / "tools").is_dir()
    scoped_registries = {
        k: v for k, v in registries.items()
        if not (tools_only and k.startswith("tests/"))
    }

    # --- coverage: every executable first-party script in repository scope is classified ---
    found: list[tuple[pathlib.Path, str, str, str]] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if _out_of_scope_path(rel):
            continue
        ok, why = _is_executable_script(p)
        if not ok:
            continue
        key = _classification_key(rel)
        found.append((p, why, rel, key))

    by_key: dict[str, tuple[pathlib.Path, str, str]] = {}
    for p, why, rel, key in found:
        if key in by_key:
            problems.append(
                f"classification key collision {key!r}: {by_key[key][2]!r} and {rel!r}"
            )
            continue
        by_key[key] = (p, why, rel)

    for key, (p, why, rel) in sorted(by_key.items()):
        if key not in scoped_registries:
            problems.append(
                f"{rel}: UNCLASSIFIED executable script (detected by {why}). Every first-party "
                "script in repository scope must be declared so a live harness cannot move to "
                "tests/ or the repository root and escape review."
            )

    for key in sorted(scoped_registries):
        if key not in by_key:
            problems.append(f"{key}: classified but no such script exists in the checked scope")

    # --- per-file checks -------------------------------------------------------------
    for key, (p, why_detected, rel) in sorted(by_key.items()):
        if key not in scoped_registries:
            continue
        suffix = p.suffix.lower()
        if suffix == ".py":
            live, why = _py_live_signal(p)
        elif suffix in (".sh", ".bash"):
            live, why = _sh_live_signal(p)
        elif suffix == ".ps1":
            live, why = _ps_live_signal(p)
        else:
            live, why = False, ""

        if live and key in NON_LIVE:
            problems.append(
                f"{rel}: classified non-live ({NON_LIVE[key]}) but behavioural discovery says "
                f"it is live ({why}); the classification is wrong"
            )
            continue

        if key in GATED:
            if suffix == ".py":
                try:
                    tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError as e:
                    problems.append(f"{rel}: cannot parse: {e}")
                    continue
                calls, suspicious = _resolved_gate_calls(tree)
                named_calls = [c for c in calls if _gate_call_matches_harness(c, key)]
                wrong_names = [
                    c for c in calls if not _gate_call_matches_harness(c, key)
                ]
                reachable, unreachable = _reachable_gate_calls(tree, named_calls)
                if not reachable:
                    detail_parts = suspicious + unreachable
                    if wrong_names:
                        detail_parts.append(
                            "resolved gate call uses a different/non-literal harness name"
                        )
                    detail = "; ".join(detail_parts) if detail_parts else "no reachable gate call"
                    problems.append(
                        f"{rel}: live entrypoint with no reachable call resolving to a safely-bound "
                        f"{GATE_MODULE}.{GATE_FUNC}({key!r}) ({detail})"
                    )
                    continue
                if suspicious:
                    problems.append(f"{rel}: {suspicious[0]}")
                conditional = GATED[key] == "conditional"
                if conditional:
                    ok_guard, reason = _guard_mentions_live_intent(tree, reachable)
                    if not ok_guard:
                        problems.append(
                            f"{rel}: gate is not on the live-intent path, so a real CAD run "
                            f"could skip it ({reason})"
                        )
                        continue
                ok_order, order_reason = _gate_precedes_live_sinks(
                    tree, reachable, conditional=conditional
                )
                if not ok_order:
                    problems.append(
                        f"{rel}: safety gate does not dominate the live operation(s) "
                        f"({order_reason})"
                    )
            elif suffix in (".sh", ".bash"):
                if not _shell_invokes_gate(p, key):
                    problems.append(
                        f"{rel}: gated entrypoint with no non-comment line invoking "
                        f"safe_process.py --gate"
                    )
            else:
                problems.append(
                    f"{rel}: classified as GATED but its type ({suffix or 'no suffix'}) cannot "
                    f"be verified for a gate call; use Python/shell or move it to LIVE_EXCEPTIONS "
                    f"with a reason"
                )

        if key in RETIRED_HARNESSES and suffix in (".sh", ".bash"):
            if not _shell_invokes_gate(p, key):
                problems.append(
                    f"{rel}: retired harness no longer routes through safe_process.py --gate, so "
                    "an old command would fail silently instead of loudly"
                )

    # --- no environment-variable bypass ----------------------------------------------
    for f in sorted(root.rglob("*.py")):
        rel = f.relative_to(root).as_posix()
        if _out_of_scope_path(rel):
            continue
        for envname in _env_reads(f):
            if "CBRIDGE_ACK" in envname:
                problems.append(
                    f"{rel}: reads environment variable {envname!r}; an environment variable "
                    "must never bypass the live-run gate"
                )

    if not problems:
        gated = sorted(GATED)
        print(f"  PASS  all {len(found)} executable script(s) classified; live entrypoints gated")
        print(f"        gated: {len(gated)} | exceptions: {len(LIVE_EXCEPTIONS)} | "
              f"mechanism: {len(GATE_MECHANISM)} | retired: {len(RETIRED_HARNESSES)} | "
              f"non-live: {len(NON_LIVE)}")
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
        for pr in problems:
            print(f"  FAIL  {pr}")
        print(f"live-gate check FAILED with {len(problems)} problem(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
