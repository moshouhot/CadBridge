#!/usr/bin/env python3
"""CadBridge P1/T01-5: is there a SYNCHRONOUS read channel while LISP is paused?

Established by tools/t01-5-pause-live-query.py (evidence: T01-5/pause-live-query.json):

  * While stopped at line 8, a COM read did not return within 40 s -> AutoCAD's main thread
    is blocked inside the LISP debugger, so Plugin CommandMethods and COM cannot serve a
    live query during a pause.
  * `evaluate` with context "repl" returned success with an EMPTY result at the time of the
    call, yet after `continue` the queued results appeared on stdout with the CORRECT values
    (circle count 1, radius 3.0, centre (100.0 100.0 0.0)).
      => "repl" evaluate is a QUEUED INJECTION, not a synchronous read. Reading this way and
         then continuing is exactly the "continue first, then query" pattern T01-5 forbids.
  * `scopes` and `variables` DID answer synchronously while paused (N=2, X=2, Y=3).
      => the adapter has a synchronous path that inspects the paused frame directly.

So the decisive question is whether `evaluate` also has a synchronous mode, selected by
context and/or frameId. VS Code's own debug UI evaluates hover/watch expressions while
stopped and displays them immediately, which suggests it must.

This probe tests every evaluate form against ONE stop and records which return a real value
without any continue. Only a form that answers synchronously can serve T01-5.

The probe also re-checks the write question properly: whether a write issued while paused is
applied before continue (which would make "writes are refused during a pause" false).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys
import time

_here = pathlib.Path(__file__).parent
_spec = importlib.util.spec_from_file_location("dap_probe", _here / "dap-probe.py")
dap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dap)

import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).parent))
import safe_process as sp  # noqa: E402  (verifiable launch/terminate; no by-name killing)


def log(m: str) -> None:
    print(m, flush=True)


def acad_pids(install_dir: str) -> list[int]:
    return [int(p["pid"]) for p in sp.install_pids(install_dir, ["acad.exe"])]


def wait_stopped(c, timeout: float) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with c.lock:
            for i, e in enumerate(c.events):
                if e.get("event") == "stopped":
                    return c.events.pop(i)
        time.sleep(0.1)
    return None


def drain_output(c) -> list[str]:
    with c.lock:
        return [e.get("body", {}).get("output", "") for e in c.events
                if e.get("event") == "output"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--acad", required=True)
    ap.add_argument("--program", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--stop-wait", type=float, default=120.0)
    ap.add_argument("--idle-wait", type=float, default=55.0)
    args = ap.parse_args()

    # DISABLED until the safety review passes: this harness uses COM / abandoned
    # worker threads and lacks mandatory HWND->owned-PID verification.
    sp.require_safety_review_passed("t01-5-sync-read-forms.py")

    install_dir = str(pathlib.Path(args.acad).parent)
    if acad_pids(install_dir):
        log("REFUSING: an acad.exe from this install is already running")
        return 2

    R: dict = {"checks": [], "evaluate_matrix": []}
    if os.path.exists(args.transcript):
        os.remove(args.transcript)

    scratch = pathlib.Path("F:/CadBridge-run/_attach-scratch") / f"t5c-{os.getpid()}"
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["ACAD_USERPROFILE"] = str(scratch)
    log(f"launching isolated CAD: {args.acad}")
    # Launch through the safe harness so ownership (pid + creation time + exe path) is
    # recorded and can be re-verified before any cleanup.
    owned_token = sp.launch_and_record(args.acad, cwd=install_dir, env=env,
                                 role="t01-5", log=log)
    owned_pid = sp.owned_process(owned_token).pid if owned_token else None
    pid = owned_pid
    if pid is None:
        log("ERROR: CAD did not start (no verifiable launched process)")
        return 3
    R["acad_pid"] = pid
    log(f"CAD pid={pid}; waiting {args.idle_wait:.0f}s for idle")
    time.sleep(args.idle_wait)

    def check(name, ok, detail=None):
        R["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        log(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail is not None else ""))

    c = dap.DapClient([args.adapter], args.transcript, timeout=args.timeout)
    try:
        r = c.call("initialize", {"clientID": "cadbridge-t5c", "clientName": "CadBridge T5c",
                                  "adapterID": "autolisp", "pathFormat": "path",
                                  "linesStartAt1": True, "columnsStartAt1": True})
        check("initialize", bool(r and r.get("success")))
        check("initialized event", c.wait_event("initialized", timeout=15) is not None)

        r = c.call("attach", {"type": "attachlisp", "request": "attach",
                              "processId": str(pid), "program": args.program}, timeout=args.timeout)
        check("attach to live PID", bool(r and r.get("success")))
        if not (r and r.get("success")):
            return 0

        r = c.call("setBreakpoints", {
            "source": {"path": args.program, "name": os.path.basename(args.program)},
            "breakpoints": [{"line": 8}], "lines": [8]})
        bps = ((r or {}).get("body") or {}).get("breakpoints") or []
        check("breakpoint line 8 verified", bool(bps and bps[0].get("verified")), bps)
        check("configurationDone", bool((c.call("configurationDone", {}) or {}).get("success")))

        time.sleep(2)
        r = c.call("evaluate", {"expression": "(C:CBDBG)", "context": "repl"}, timeout=args.timeout)
        check("trigger (C:CBDBG)", bool(r and r.get("success")))

        ev = wait_stopped(c, args.stop_wait)
        check("stopped at line 8", ev is not None, (ev or {}).get("body"))
        if ev is None:
            return 0
        tid = (ev.get("body") or {}).get("threadId", 1)

        r = c.call("stackTrace", {"threadId": tid})
        frames = ((r or {}).get("body") or {}).get("stackFrames") or []
        f = next((x for x in frames if (x.get("source") or {}).get("path")), None)
        frame_id = f.get("id") if f else None
        R["stop_frame_id"] = frame_id
        check("stop frame has an id", frame_id is not None, f)
        drain_output(c)

        # The expression reads the drawing through the LISP evaluator. If any form answers
        # synchronously with "3", a live pause-state read is achievable.
        expr = '(cdr (assoc 40 (entget (entlast))))'
        matrix = [
            ("repl",        None,     "repl"),
            ("repl+frame",  frame_id, "repl"),
            ("watch+frame", frame_id, "watch"),
            ("hover+frame", frame_id, "hover"),
            ("watch",       None,     "watch"),
            ("hover",       None,     "hover"),
            ("clipboard+frame", frame_id, "clipboard"),
        ]
        for label, fid, ctx in matrix:
            a: dict = {"expression": expr, "context": ctx}
            if fid is not None:
                a["frameId"] = fid
            before = drain_output(c)
            t0 = time.time()
            rr = c.call("evaluate", a, timeout=args.timeout)
            dt = round(time.time() - t0, 3)
            body = (rr or {}).get("body") or {}
            after = drain_output(c)
            entry = {
                "label": label, "context": ctx, "frameId": fid,
                "success": (rr or {}).get("success"),
                "result": body.get("result"),
                "type": body.get("type"),
                "message": (rr or {}).get("message"),
                "seconds": dt,
                "new_output": [x for x in after if x not in before],
            }
            R["evaluate_matrix"].append(entry)
            log(f"  [{label}] success={entry['success']} result={entry['result']!r} "
                f"type={entry['type']!r} {dt}s new_output={entry['new_output']}")

        sync = [e for e in R["evaluate_matrix"]
                if e["success"] and e["result"] not in (None, "") and str(e["result"]).strip() == "3"]
        R["synchronous_read_forms"] = [e["label"] for e in sync]
        check("at least one evaluate form answers synchronously while paused",
              len(sync) > 0, [e["label"] for e in sync])

        # ---- write during pause: is it applied before continue? --------------
        marker = '(entmakex (list (cons 0 "CIRCLE") (cons 10 (list 5.0 5.0 0.0)) (cons 40 77.0)))'
        before_w = drain_output(c)
        rw = c.call("evaluate", {"expression": marker, "context": "repl"}, timeout=args.timeout)
        time.sleep(3)
        # Read the entity count via a synchronous form if we found one, else via locals.
        probe = None
        if sync:
            label = sync[0]["label"]
            ctx = next(e["context"] for e in R["evaluate_matrix"] if e["label"] == label)
            fid = next(e["frameId"] for e in R["evaluate_matrix"] if e["label"] == label)
            pa: dict = {"expression": '(sslength (ssget "X" (list (cons 0 "CIRCLE"))))', "context": ctx}
            if fid is not None:
                pa["frameId"] = fid
            pr = c.call("evaluate", pa, timeout=args.timeout)
            probe = {"label": label, "success": (pr or {}).get("success"),
                     "result": ((pr or {}).get("body") or {}).get("result")}
            log(f"  write-probe via {label}: {probe}")
        R["write_while_paused"] = {
            "expression": marker, "success": (rw or {}).get("success"),
            "result": ((rw or {}).get("body") or {}).get("result"),
            "count_probe_before_continue": probe,
            "new_output": [x for x in drain_output(c) if x not in before_w],
        }
        log(f"  write while paused -> success={R['write_while_paused']['success']} "
            f"count_before_continue={probe}")

        # ---- resume and see what actually happened --------------------------
        r = c.call("continue", {"threadId": tid}, timeout=args.timeout)
        check("continue accepted", bool(r and r.get("success")))
        time.sleep(7)
        R["output_after_continue"] = drain_output(c)
        log(f"  output after continue: {R['output_after_continue']}")
        check("CBDBG_DONE printed after continue",
              any("CBDBG_DONE" in o for o in R["output_after_continue"]))

        # Whether the paused write was applied is decided by the queued results: if a new
        # entity handle appears, the write took effect.
        R["paused_write_was_applied"] = any(
            "图元名" in o or "Entity" in o or "2D" in o for o in R["output_after_continue"])

        return 0
    finally:
        c.close()
        R["adapter_stderr"] = c.stderr_lines[:30]
        log(f"terminating only our CAD pid={pid}")
        # Verified-ownership termination (pid + creation time + exe path). Refuses if the
        # recorded identity no longer matches, e.g. after Windows reused the pid.
        cleanup_ok = sp.terminate_owned(owned_token, log=log)   # token only
        check("owned CAD cleanup confirmed", cleanup_ok)
        R["cleanup_confirmed"] = bool(cleanup_ok)
        pathlib.Path(args.out).write_text(json.dumps(R, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
        ok = sum(1 for x in R["checks"] if x["ok"])
        bad = sum(1 for x in R["checks"] if not x["ok"])
        log(f"\nchecks: {ok} passed, {bad} failed")
        log(f"results: {args.out}")
        if sys.exc_info()[0] is None:
            raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    raise SystemExit(main())
