#!/usr/bin/env python3
"""Localize and describe the AutoCAD AutoLISP DAP surface on a real installation.

Two modes:

  probe <binary>            -- describe DAP tokens present in one binary
  localize <autocad_dir>    -- walk an AutoCAD install and report which file(s)
                               actually contain the DAP request/event vocabulary

Read-only. Never executes AutoCAD or the debug adapter.

Why this exists: the file named `AutoLispDebugAdapter.exe` is NOT where the DAP
vocabulary lives -- it is a thin session host that carries only the handshake and
the processId/program attach arguments. The request handlers (setBreakpoints,
stackTrace, scopes, variables, evaluate, stepIn, ...) live in AutoCAD's in-process
VLISP module (`vl_u.crx`). Knowing this before writing a debug client prevents
P1 from designing against the wrong process.

Usage:
  python tools/probe-debug-adapter.py probe "D:\\...\\AutoLispDebugAdapter.exe"
  python tools/probe-debug-adapter.py localize "D:\\Program Files\\Autodesk\\AutoCAD 2026" --json out.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

# DAP vocabulary grouped by role. These are matched as exact byte sequences.
DAP_VOCAB = {
    "handshake": ["initialize", "initialized", "configurationDone", "attach", "launch",
                  "disconnect", "terminate"],
    "requests": ["setBreakpoints", "setFunctionBreakpoints", "setExceptionBreakpoints",
                 "stackTrace", "scopes", "variables", "evaluate", "continue", "next",
                 "stepIn", "stepOut", "threads", "source", "pause"],
    "events": ["stopped", "output", "terminated", "exited", "breakpoint", "thread"],
    "fields": ["variablesReference", "frameId", "threadId", "sourceModified", "program",
               "processId", "lines", "Content-Length"],
    "capabilities": ["supportsConfigurationDoneRequest", "supportsEvaluateForHovers",
                     "supportsConditionalBreakpoints", "supportsHitConditionalBreakpoints",
                     "supportsFunctionBreakpoints", "supportsSetVariable", "supportsLogPoints",
                     "exceptionBreakpointFilters"],
    "autodesk": ["runtimeerror", "AutoLispDebugAdapter", "asilisp", "vldebug", "VLISP"],
}

# Tokens that indicate the full in-process debug engine, not just a session host.
ENGINE_MARKERS = ["setBreakpoints", "stackTrace", "scopes", "variables", "evaluate",
                  "stepIn", "stepOut", "configurationDone", "variablesReference"]

MAX_FILE_BYTES = 120 * 1024 * 1024


def scan_bytes(data: bytes) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for group, toks in DAP_VOCAB.items():
        hits = []
        for t in toks:
            n = data.count(t.encode("ascii")) + data.count(t.encode("utf-16-le"))
            if n:
                hits.append(f"{t}({n})")
        if hits:
            out[group] = hits
    return out


def engine_score(findings: dict[str, list[str]]) -> int:
    joined = " ".join(v for vs in findings.values() for v in vs)
    return sum(1 for m in ENGINE_MARKERS if m in joined)


def cmd_probe(args) -> int:
    p = Path(args.target)
    if not p.is_file():
        print(f"ERROR: not a file: {p}", file=sys.stderr)
        return 2
    data = p.read_bytes()
    findings = scan_bytes(data)
    score = engine_score(findings)
    report = {
        "mode": "probe",
        "path": str(p),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "findings": findings,
        "engine_marker_score": score,
        "role": "full_dap_engine" if score >= 5 else ("session_host_or_partial" if score else "no_dap_vocabulary"),
        "evidence_level": "STATIC_BINARY_STRINGS_ONLY",
        "disclaimer": ("Static strings prove the binary contains these tokens. They do not prove "
                       "wire behaviour, ordering, or semantics. Only a real DAP session can."),
    }
    emit(report, args.json)
    return 0


def cmd_localize(args) -> int:
    root = Path(args.target)
    if not root.is_dir():
        print(f"ERROR: not a directory: {root}", file=sys.stderr)
        return 2
    candidates = []
    scanned = skipped = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            fp = Path(dirpath) / f
            try:
                size = fp.stat().st_size
            except OSError:
                skipped += 1
                continue
            if size == 0 or size > MAX_FILE_BYTES:
                skipped += 1
                continue
            try:
                data = fp.read_bytes()
            except OSError:
                skipped += 1
                continue
            scanned += 1
            findings = scan_bytes(data)
            score = engine_score(findings)
            if score >= 1:
                candidates.append({
                    "path": str(fp),
                    "size_bytes": size,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "engine_marker_score": score,
                    "role": "full_dap_engine" if score >= 5 else "partial",
                    "findings": findings,
                })
    candidates.sort(key=lambda c: (-c["engine_marker_score"], c["path"]))
    report = {
        "mode": "localize",
        "root": str(root),
        "files_scanned": scanned,
        "files_skipped": skipped,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "conclusion": (
            "The full DAP request vocabulary is located in the highest-scoring candidate. "
            "In AutoCAD 2022/2024/2026 this is vl_u.crx (in-process VLISP module); "
            "AutoLispDebugAdapter.exe carries only handshake + processId/program."
            if candidates else
            "No file in this installation contains DAP vocabulary."
        ),
        "evidence_level": "STATIC_BINARY_STRINGS_ONLY",
    }
    emit(report, args.json)
    return 0


def emit(report: dict, json_out: str | None) -> None:
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if json_out:
        Path(json_out).write_text(text, encoding="utf-8")
        print(f"WROTE {json_out}")
    else:
        print(text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    p1 = sub.add_parser("probe")
    p1.add_argument("target")
    p2 = sub.add_parser("localize")
    p2.add_argument("target")
    for s in (p1, p2):
        s.add_argument("--json", dest="json", default=None)
    args = ap.parse_args()
    return cmd_probe(args) if args.mode == "probe" else cmd_localize(args)


if __name__ == "__main__":
    raise SystemExit(main())
