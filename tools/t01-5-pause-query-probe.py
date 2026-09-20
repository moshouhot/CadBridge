#!/usr/bin/env python3
"""CadBridge P1/T01-5: is a pause-state live query architecturally possible?

T01-5 is the one P1 item that must NOT be silently downgraded. The question it asks is:

    While AutoLISP is stopped at a breakpoint, can the drawing be read *legally*,
    from the same stop, without deadlocking?

Before building any Plugin-side plumbing, one architectural fact decides whether that is
possible at all:

    Is AutoCAD's main thread blocked while the LISP debugger is paused?

If the main thread is blocked, then:
  * a CommandMethod (which runs on the main thread) can never execute, and
  * COM calls, which marshal to the main thread, will hang,
so T01-5 could only be served by a stale snapshot -- which the test plan says must be
reported as BLOCKED, not PASS.

If the main thread is NOT blocked, a legal live query is at least feasible.

This script measures that fact directly and safely:

  1. Launch an isolated AutoCAD (only our PID is ever touched).
  2. Attach the official DAP adapter to it, break at line 8 of the fixture.
     At that stop, line 7 has already run (circle r=3 created) and line 8
     (princ "CBDBG_DONE") has not.
  3. While stopped, from THIS process, attempt a COM read of the drawing on a worker
     thread with a hard timeout, so a deadlock is measured rather than suffered.
  4. Record exactly what happened, then continue and confirm the program finished.

The COM read is deliberately done from a *different* process than the paused host, because
that is what CadBridge's Host would do, and because a same-thread read would prove nothing.

Evidence written: a JSON result plus the raw DAP transcript.
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
import bounded_worker as bw  # noqa: E402


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--acad", required=True)
    ap.add_argument("--program", required=True)
    ap.add_argument("--progid", default="AutoCAD.Application.25.1")
    ap.add_argument("--out", required=True)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--stop-wait", type=float, default=120.0)
    ap.add_argument("--idle-wait", type=float, default=55.0)
    ap.add_argument("--com-timeout", type=float, default=45.0)
    args = ap.parse_args()

    # DISABLED until the safety review passes: this harness uses COM / abandoned
    # worker threads and lacks mandatory HWND->owned-PID verification.
    sp.require_safety_review_passed("t01-5-pause-query-probe.py")

    install_dir = str(pathlib.Path(args.acad).parent)
    if acad_pids(install_dir):
        log("REFUSING: an acad.exe from this install is already running")
        return 2

    results: dict = {"question": "is a pause-state live query architecturally possible?",
                     "checks": [], "com_read_while_paused": None, "com_read_after_continue": None}
    if os.path.exists(args.transcript):
        os.remove(args.transcript)

    scratch = pathlib.Path("F:/CadBridge-run/_attach-scratch") / f"t5-{os.getpid()}"
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
    results["acad_pid"] = pid
    log(f"CAD pid={pid}; waiting {args.idle_wait:.0f}s for idle")
    time.sleep(args.idle_wait)

    def check(name, ok, detail=None):
        results["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        log(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail is not None else ""))

    c = dap.DapClient([args.adapter], args.transcript, timeout=args.timeout)
    try:
        r = c.call("initialize", {
            "clientID": "cadbridge-t5", "clientName": "CadBridge T5",
            "adapterID": "autolisp", "pathFormat": "path",
            "linesStartAt1": True, "columnsStartAt1": True,
        })
        check("initialize", bool(r and r.get("success")))
        check("initialized event", c.wait_event("initialized", timeout=15) is not None)

        r = c.call("attach", {"type": "attachlisp", "request": "attach",
                              "processId": str(pid), "program": args.program},
                   timeout=args.timeout)
        check("attach to live PID", bool(r and r.get("success")))
        if not (r and r.get("success")):
            return 0

        # Break at line 8: line 7 (entmakex circle) has run, line 8 (princ) has not.
        r = c.call("setBreakpoints", {
            "source": {"path": args.program, "name": os.path.basename(args.program)},
            "breakpoints": [{"line": 8}], "lines": [8],
        })
        bps = ((r or {}).get("body") or {}).get("breakpoints") or []
        check("breakpoint line 8 verified", bool(bps and bps[0].get("verified")), bps)

        r = c.call("configurationDone", {})
        check("configurationDone", bool(r and r.get("success")))

        time.sleep(2)
        r = c.call("evaluate", {"expression": "(C:CBDBG)", "context": "repl"}, timeout=args.timeout)
        check("trigger (C:CBDBG)", bool(r and r.get("success")))

        ev = wait_stopped(c, args.stop_wait)
        check("stopped at line 8", ev is not None, (ev or {}).get("body"))
        if ev is None:
            return 0
        results["stop_event"] = ev.get("body")
        tid = (ev.get("body") or {}).get("threadId", 1)

        r = c.call("stackTrace", {"threadId": tid})
        frames = ((r or {}).get("body") or {}).get("stackFrames") or []
        results["frames_at_stop"] = frames
        f = next((x for x in frames if (x.get("source") or {}).get("path")), None)
        results["stop_line"] = f.get("line") if f else None
        log(f"  stopped at line {results['stop_line']}")

        # Output so far: CBDBG_DONE must NOT be present yet.
        results["output_at_stop"] = [e.get("body", {}).get("output", "")
                                     for e in c.events if e.get("event") == "output"]
        done_seen = any("CBDBG_DONE" in o for o in results["output_at_stop"])
        check("CBDBG_DONE not yet printed at stop", not done_seen, results["output_at_stop"])

        # ---------- THE DECISIVE MEASUREMENT ------------------------------------
        log(f"  *** attempting COM read WHILE PAUSED (hard timeout {args.com_timeout:.0f}s) ***")
        res = bw.run_owned_com_read(owned_token, args.progid, args.com_timeout)
        waited = res.get("elapsed_seconds")
        if res.get("status") == "timed_out":
            log(f"  COM worker timed out in {waited}s; worker exit confirmed={res.get('worker_exit_confirmed')}")
        else:
            log(f"  COM worker returned in {waited}s -> {res.get('status')}")
        results["com_read_while_paused"] = res
        check("COM read while paused returned",
              res.get("status") == "completed" and (res.get("result") or {}).get("status") == "ok", res)

        # ---------- control: same read after continue ---------------------------
        r = c.call("continue", {"threadId": tid})
        check("continue accepted while paused", bool(r and r.get("success")))
        time.sleep(6)
        results["output_after_continue"] = [e.get("body", {}).get("output", "")
                                            for e in c.events if e.get("event") == "output"]
        log(f"  output after continue: {results['output_after_continue']}")

        log(f"  control COM read after continue (timeout {args.com_timeout:.0f}s)")
        res2 = bw.run_owned_com_read(owned_token, args.progid, args.com_timeout)
        snap2 = res2.get("result") or {}
        if res2.get("status") == "timed_out":
            log("  control COM worker also timed out")
        else:
            log(f"  control read -> {res2.get('status')}/{snap2.get('status')} circles={snap2.get('circles')}")
        results["com_read_after_continue"] = res2

        results["verdict"] = (
            "MAIN_THREAD_BLOCKED_WHILE_PAUSED"
            if res.get("status") == "timed_out"
            else ("LIVE_QUERY_FEASIBLE"
                  if res.get("status") == "completed" and (res.get("result") or {}).get("status") == "ok"
                  else "INCONCLUSIVE")
        )
        log(f"\nVERDICT: {results['verdict']}")
        return 0
    finally:
        c.close()
        results["adapter_stderr"] = c.stderr_lines[:30]
        log(f"terminating only our CAD pid={pid}")
        # Verified-ownership termination (pid + creation time + exe path). Refuses if the
        # recorded identity no longer matches, e.g. after Windows reused the pid.
        sp.terminate_owned(owned_token, log=log)   # token only; a dict cannot authorize a kill
        pathlib.Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
        log(f"results: {args.out}")
        failed = sum(1 for x in results["checks"] if not x["ok"])
        if sys.exc_info()[0] is None:
            raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    raise SystemExit(main())
