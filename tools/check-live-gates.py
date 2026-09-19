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
    "com_read_worker.py": "child worker that reads an already-verified instance; owns nothing",
    "make-manifest.py": "hashes evidence files; no process launch",
    "compare-security-baseline.py": "diffs two JSON baselines; no process launch",
    "test-repl-probe-offline.py": "offline fake-based tests; no CAD is started",
    "selftest.sh": (
        "runs the suite. It only INVOKES the harness scripts (argument-validation cases) and "
        "writes a fake accoreconsole stand-in with a heredoc redirect; it never executes a real "
        "CAD binary."
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
            a = node.args
            params = list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)
            if a.vararg:
                params.append(a.vararg)
            if a.kwarg:
                params.append(a.kwarg)
            label = getattr(node, "name", "lambda")
            for prm in params:
                if prm.arg in names:
                    note(prm.arg, f"shadowed by parameter of {label}() at line {node.lineno}")
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


def _guard_mentions_live_intent(tree: ast.AST, calls: list[ast.Call]) -> tuple[bool, str]:
    """Does a resolved gate call sit inside a branch taken WHEN LIVE INTENT IS TRUE?

    Polarity matters. Searching the rendered condition for the text 'live' accepted
    `if not live_intent:` -- where the gate runs only on the OFFLINE path and is skipped for a
    real CAD run, the exact opposite of what is wanted. The condition is therefore inspected
    structurally: the gate must be reachable when the live-intent expression is truthy.
    """
    call_lines = {c.lineno for c in calls}

    def contains_gate(body: list[ast.stmt]) -> bool:
        for stmt in body:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Call) and sub.lineno in call_lines:
                    return True
        return False

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        # Only conditions that actually reference live intent are considered.
        names = {n.id for n in ast.walk(test) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(test) if isinstance(n, ast.Attribute)}
        rendered = ast.unparse(test)
        if not ("live_intent" in names or "live_intent" in attrs or "live_intent" in rendered):
            continue

        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            # `if not live_intent:` -- the body is the OFFLINE path. A gate there is wrong.
            if contains_gate(node.body):
                return False, (f"line {node.lineno}: gate runs when live intent is FALSE "
                               f"(`if not live_intent:`), so a live CAD run skips it")
            # An `else` branch of a negated test IS the live path.
            if node.orelse and contains_gate(node.orelse):
                return True, ""
            continue

        # Positive condition: the body is the live path.
        if contains_gate(node.body):
            return True, ""

    return False, "no gate call is reachable on the live-intent path"


def _shell_invokes_gate(path: pathlib.Path) -> bool:
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "safe_process.py" in line and "--gate" in line:
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
        if nm in LIVE_LAUNCH_CALLS:
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


def _sh_live_signal(path: pathlib.Path) -> tuple[bool, str]:
    cad_exe = re.compile(r"(acad|accoreconsole|AutoLispDebugAdapter)\.exe$", re.I)
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^[A-Za-z_][A-Za-z0-9_]*=(.+)$", line)
        if m:
            if cad_exe.search(m.group(1).strip().strip("\"'")):
                return True, "assigns a CAD executable path"
            continue
        if cad_exe.search(line.split()[0].strip("\"'")):
            return True, "executes a CAD executable"
    return False, ""


def _ps_live_signal(path: pathlib.Path) -> tuple[bool, str]:
    """PowerShell: does this actually START a CAD host?

    Deliberately narrow, because PowerShell has many ways to merely NAME an executable:
    `Get-ChildItem -Filter 'acad.exe'`, `Test-Path`, `Get-FileHash`, and invoking a scriptblock
    such as `& $probe 'AutoLispDebugAdapter.exe'` (which tests for the file's PRESENCE, not
    runs it -- a false positive that this function previously produced for
    inventory-autocad.ps1).

    Only these count:
      * Start-Process / Invoke-Item / saps targeting a CAD executable
      * a call operator (&) whose FIRST token is a CAD executable path
      * New-Object -ComObject for AutoCAD
    """
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if re.search(r"\b(Start-Process|saps|Invoke-Item)\b[^;]*\b(acad|accoreconsole|AutoLispDebugAdapter)(\.exe)?\b", line, re.I):
            return True, "starts a CAD executable"
        # Call operator whose target is a CAD executable: `& "C:\...\acad.exe" args`
        m = re.match(r"^&\s+[\"']?([^\"'\s]+)", line)
        if m and re.search(r"(acad|accoreconsole|AutoLispDebugAdapter)\.exe$", m.group(1), re.I):
            return True, "call operator on a CAD executable"
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


def check(tools: pathlib.Path) -> list[str]:
    problems: list[str] = []
    registries = _all_registries()

    # --- coverage: every executable script at any depth must be classified -----------
    #
    # Classification is keyed by the path RELATIVE to tools/, not by basename. Keying by
    # basename let `subdir/safe_process.py` inherit the GATE_MECHANISM entry for
    # `safe_process.py`, so a live harness could pick a colliding name and be neither gated
    # nor rejected. Basename collisions across different directories are also rejected, so a
    # registry entry can never silently cover two files.
    found: list[tuple[pathlib.Path, str]] = []
    for p in sorted(tools.rglob("*")):
        if not p.is_file():
            continue
        ok, why = _is_executable_script(p)
        if ok:
            found.append((p, why))

    by_rel = {p.relative_to(tools).as_posix(): (p, why) for p, why in found}
    by_name: dict[str, list[str]] = {}
    for rel in by_rel:
        by_name.setdefault(pathlib.PurePosixPath(rel).name, []).append(rel)

    for rel, (p, why) in sorted(by_rel.items()):
        if rel not in registries:
            problems.append(
                f"{rel}: UNCLASSIFIED executable script (detected by {why}). Every script must "
                f"be declared in GATED, LIVE_EXCEPTIONS, GATE_MECHANISM, RETIRED_HARNESSES or "
                f"NON_LIVE, so a new live harness cannot slip in unexamined."
            )

    for name in sorted(registries):
        if name not in by_rel:
            problems.append(f"{name}: classified but no such script exists (stale registry entry)")

    # Reject basename collisions: two different paths sharing a name would make a basename-keyed
    # registry ambiguous, and a nested file could inherit another file's classification.
    for base, rels in sorted(by_name.items()):
        if len(rels) > 1 and base in registries:
            problems.append(
                f"basename collision for {base!r}: {rels}. The registry is keyed by basename, so "
                f"a nested file would inherit another file's classification. Rename one, or key "
                f"the registry by path."
            )

    # --- per-file checks -------------------------------------------------------------
    for rel, (p, why_detected) in sorted(by_rel.items()):
        name = pathlib.PurePosixPath(rel).name
        if rel not in registries:
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

        if live and rel in NON_LIVE:
            problems.append(
                f"{rel}: classified non-live ({NON_LIVE[rel]}) but behavioural discovery says "
                f"it is live ({why}); the classification is wrong"
            )
            continue

        if rel in GATED:
            if suffix == ".py":
                try:
                    tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError as e:
                    problems.append(f"{rel}: cannot parse: {e}")
                    continue
                calls, suspicious = _resolved_gate_calls(tree)
                if not calls:
                    detail = ("; ".join(suspicious) if suspicious
                              else "no call to the gate at all")
                    problems.append(
                        f"{rel}: live entrypoint with no call resolving to a safely-bound "
                        f"{GATE_MODULE}.{GATE_FUNC}() ({detail})"
                    )
                    continue
                if suspicious:
                    problems.append(f"{rel}: {suspicious[0]}")
                if GATED[rel] == "conditional":
                    ok_guard, reason = _guard_mentions_live_intent(tree, calls)
                    if not ok_guard:
                        problems.append(
                            f"{rel}: gate is not on the live-intent path, so a real CAD run "
                            f"could skip it ({reason})"
                        )
            elif suffix in (".sh", ".bash"):
                if not _shell_invokes_gate(p):
                    problems.append(
                        f"{rel}: gated entrypoint with no non-comment line invoking "
                        f"safe_process.py --gate"
                    )
            else:
                # A gated entrypoint that is neither Python nor shell (e.g. a shebang script
                # with no suffix) cannot be verified, so refuse rather than assume.
                problems.append(
                    f"{rel}: classified as GATED but its type ({suffix or 'no suffix'}) cannot "
                    f"be verified for a gate call; use Python/shell or move it to LIVE_EXCEPTIONS "
                    f"with a reason"
                )

        if rel in RETIRED_HARNESSES and suffix in (".sh", ".bash"):
            if not _shell_invokes_gate(p):
                problems.append(
                    f"{rel}: retired harness no longer routes through safe_process.py --gate, so "
                    f"an old command would fail silently instead of loudly"
                )

    # --- no environment-variable bypass ----------------------------------------------
    for f in sorted(tools.rglob("*.py")):
        for envname in _env_reads(f):
            if "CBRIDGE_ACK" in envname:
                problems.append(
                    f"{f.relative_to(tools)}: reads environment variable {envname!r} (directly, "
                    f"via an alias, or as a literal); an environment variable must never bypass "
                    f"the live-run gate"
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
