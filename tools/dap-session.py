#!/usr/bin/env python3
"""CadBridge P1/T01-3: drive a FULL AutoLISP debug session and record the transcript.

Established facts this script builds on (all measured, see T01-3/report.md):
  * `AutoLispDebugAdapter.exe` is a real DAP server using Content-Length framing.
  * `initialize` + `initialized` work with no CAD present.
  * `attach {processId}` gets NO RESPONSE even with a live CAD.
  * Launching the adapter as `adapter -- <acad.exe>` and then sending `launch {program}`
    DOES work: launch, setBreakpoints (verified:true), configurationDone and threads all
    succeed, and the adapter spawns AutoCAD itself.

So the supported topology is: the ADAPTER owns the host process. This script follows that
topology to reach a real `stopped` event and read real stack/scopes/variables.

Evidence produced: a JSONL transcript of every byte in/out, plus a decoded summary.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import importlib.util

_spec = importlib.util.spec_from_file_location("dap_probe", pathlib.Path(__file__).parent / "dap-probe.py")
dap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dap)

import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).parent))
import safe_process as sp  # noqa: E402


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    # DISABLED until the safety review passes. This harness causes a live AutoCAD
    # session (the adapter spawns the host), so it is gated like the other live entrypoints.
    sp.require_safety_review_passed("dap-session.py")
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--product", required=True, help="path to acad.exe (the adapter's -- argument)")
    ap.add_argument("--program", required=True, help="AutoLISP source file to debug")
    ap.add_argument("--breakpoint", action="append", default=[])
    ap.add_argument("--run-command", default=None,
                    help="Lisp command name to execute, e.g. C:CBDBG")
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--wait-stopped", type=float, default=90.0)
    args = ap.parse_args()

    if os.path.exists(args.transcript):
        os.remove(args.transcript)

    log(f"adapter : {args.adapter}")
    log(f"product : {args.product}")
    log(f"program : {args.program}")
    c = dap.DapClient([args.adapter, "--", args.product], args.transcript, timeout=args.timeout)
    results: dict = {"steps": []}

    def record(step: str, ok: bool, detail=None):
        results["steps"].append({"step": step, "ok": ok, "detail": detail})
        log(f"  [{'OK ' if ok else 'FAIL'}] {step}" + (f" :: {detail}" if detail else ""))

    try:
        r = c.call("initialize", {
            "clientID": "cadbridge-p1-probe", "clientName": "CadBridge P1 DAP Probe",
            "adapterID": "autolisp", "pathFormat": "path",
            "linesStartAt1": True, "columnsStartAt1": True, "supportsVariableType": True,
        })
        caps = (r or {}).get("body") or {}
        record("initialize", bool(r and r.get("success")), caps)
        results["capabilities"] = caps

        ev = c.wait_event("initialized", timeout=10)
        record("initialized event", ev is not None)

        r = c.call("launch", {"program": args.program, "type": "autolisp", "request": "launch"})
        record("launch", bool(r and r.get("success")), (r or {}).get("body"))

        for bp in args.breakpoint:
            path, _, line = bp.rpartition(":")
            r = c.call("setBreakpoints", {
                "source": {"path": path, "name": os.path.basename(path)},
                "breakpoints": [{"line": int(line)}],
                "lines": [int(line)],
            })
            bps = ((r or {}).get("body") or {}).get("breakpoints") or []
            record(f"setBreakpoints {path}:{line}", bool(r and r.get("success")), bps)

        r = c.call("configurationDone", {})
        record("configurationDone", bool(r and r.get("success")))

        # Give the host time to start, then ask it to run the fixture command.
        time.sleep(3)
        r = c.call("threads", {})
        record("threads", bool(r and r.get("success")), (r or {}).get("body"))

        # Drain any output events the adapter emitted during startup.
        time.sleep(2)
        outputs = [e for e in c.events if e.get("event") == "output"]
        results["startup_output"] = [e.get("body", {}).get("output", "") for e in outputs]
        for o in results["startup_output"]:
            log(f"  [output] {o.strip()!r}")

        if args.run_command:
            # Ask the target to execute the Lisp command so the breakpoint can be hit.
            log(f"  evaluating ({args.run_command}) to trigger the breakpoint")
            r = c.call("evaluate", {
                "expression": f"({args.run_command})",
                "context": "repl",
            }, timeout=args.timeout)
            record("evaluate to trigger command", bool(r and r.get("success")), (r or {}).get("body"))

        log(f"  waiting up to {args.wait_stopped:.0f}s for 'stopped' ...")
        ev = c.wait_event("stopped", timeout=args.wait_stopped)
        if ev is None:
            record("stopped event", False, "no stopped event")
            results["stopped"] = None
        else:
            body = ev.get("body") or {}
            record("stopped event", True, body)
            results["stopped"] = body
            tid = body.get("threadId", 1)

            r = c.call("stackTrace", {"threadId": tid})
            frames = ((r or {}).get("body") or {}).get("stackFrames") or []
            record("stackTrace", bool(r and r.get("success")), frames)
            results["stack_frames"] = frames

            if frames:
                r = c.call("scopes", {"frameId": frames[0]["id"]})
                scopes = ((r or {}).get("body") or {}).get("scopes") or []
                record("scopes", bool(r and r.get("success")), scopes)
                results["scopes"] = scopes

                all_vars = []
                for sc in scopes:
                    ref = sc.get("variablesReference")
                    if not ref:
                        continue
                    vr = c.call("variables", {"variablesReference": ref})
                    vs = ((vr or {}).get("body") or {}).get("variables") or []
                    all_vars.append({"scope": sc.get("name"), "variables": vs})
                results["variables"] = all_vars
                record("variables", bool(all_vars), all_vars)

            for cmd in ("stepIn", "next", "stepOut", "continue"):
                r = c.call(cmd, {"threadId": tid}, timeout=args.timeout)
                record(cmd, bool(r and r.get("success")), (r or {}).get("body"))
                time.sleep(1.5)
                c.wait_event(f"__nonexistent_{cmd}", timeout=0.1)  # no-op to drain
                stopped_again = [e for e in c.events if e.get("event") == "stopped"]
                results.setdefault("stopped_events", []).extend(
                    [e.get("body") for e in stopped_again])
                if cmd != "continue":
                    break  # one step is enough to prove control works

        time.sleep(1)
        results["adapter_alive"] = c.proc.poll() is None
        return 0
    finally:
        c.close()
        results["adapter_stderr"] = c.stderr_lines[:50]
        out = pathlib.Path(args.transcript).with_suffix(".summary.json")
        out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"\nsummary written: {out}")
        log(f"adapter alive at end: {results.get('adapter_alive')}")


if __name__ == "__main__":
    raise SystemExit(main())
