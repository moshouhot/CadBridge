#!/usr/bin/env python3
"""CadBridge P1/T01-5 (core, non-degradable): pause-state live query, measured properly.

WHY THIS PROBE LOOKS THE WAY IT DOES
------------------------------------
A first attempt (tools/t01-5-pause-query-probe.py) established one hard fact and revealed a
bug in its own verdict logic:

  FACT   While LISP was stopped at line 8, a COM read of the drawing did not return within
         45 s, then returned only after `continue`. After continue the read succeeded and
         reported handle=2CE r=3.0 -- so line 7's circle really existed during the pause.
  BUG    The still-blocked worker thread later overwrote `result["status"]`, so the verdict
         printed LIVE_QUERY_FEASIBLE even though the measurement said the opposite. The race
         is fixed here by snapshotting the outcome at decision time and never letting the
         worker mutate a field the decision depends on.

The fact matters because it decides the whole design of T01-5:

  * A CommandMethod runs on AutoCAD's main thread.
  * COM calls marshal to the main thread.
  * If the main thread is inside the LISP debugger wait loop, NEITHER can run.

So the obvious channel (Plugin command / COM) is unavailable exactly when T01-5 needs it.
That does not automatically mean T01-5 is impossible -- it means the live query has to use
the channel that is still alive during a pause: the DAP/LISP channel itself. The test plan
forbids only two things: passing via "continue first, then query", and faking a stop. Reading
the drawing from *inside* the paused LISP context is neither of those.

This probe therefore measures, all at ONE stop_id, in this order:

  A. COM read while paused            -> expect timeout (main thread blocked)
  B. DAP evaluate reading the drawing -> does the paused LISP context see the circle?
  C. DAP evaluate performing a WRITE  -> is a normal DB write refused (DEBUG_BUSY)?
  D. second concurrent DAP client     -> is the adapter single-session?
  E. stale handle after continue      -> do old frame ids stop resolving?
  F. step/continue still work         -> the pause was not wedged
  G. COM read after continue          -> the control that proves the circle is real

Every wait is bounded, and the blocked COM worker is a daemon thread whose result is
snapshotted before the decision is made.
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


def drain_output(c) -> list[str]:
    with c.lock:
        return [e.get("body", {}).get("output", "") for e in c.events
                if e.get("event") == "output"]


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
    ap.add_argument("--com-timeout", type=float, default=40.0)
    args = ap.parse_args()

    # DISABLED until the safety review passes: this harness uses COM / abandoned
    # worker threads and lacks mandatory HWND->owned-PID verification.
    sp.require_safety_review_passed("t01-5-pause-live-query.py")

    install_dir = str(pathlib.Path(args.acad).parent)
    if acad_pids(install_dir):
        log("REFUSING: an acad.exe from this install is already running")
        return 2

    R: dict = {"checks": [], "findings": {}}
    if os.path.exists(args.transcript):
        os.remove(args.transcript)

    scratch = pathlib.Path("F:/CadBridge-run/_attach-scratch") / f"t5b-{os.getpid()}"
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
        r = c.call("initialize", {"clientID": "cadbridge-t5b", "clientName": "CadBridge T5b",
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
        stop_body = ev.get("body") or {}
        tid = stop_body.get("threadId", 1)
        R["findings"]["stop_event"] = stop_body

        r = c.call("stackTrace", {"threadId": tid})
        frames = ((r or {}).get("body") or {}).get("stackFrames") or []
        f = next((x for x in frames if (x.get("source") or {}).get("path")), None)
        frame_id = f.get("id") if f else None
        R["findings"]["stop_frame"] = f
        check("stopped in a real source frame", f is not None, f)
        R["findings"]["output_before_stop"] = drain_output(c)
        check("CBDBG_DONE not printed before stop",
              not any("CBDBG_DONE" in o for o in R["findings"]["output_before_stop"]),
              R["findings"]["output_before_stop"])

        # ================= A. COM read while paused ==========================
        log(f"  [A] COM read WHILE PAUSED (bounded {args.com_timeout:.0f}s)")
        com_a = bw.run_owned_com_read(owned_token, args.progid, args.com_timeout)
        R["findings"]["A_com_read_while_paused"] = com_a
        log(f"      worker={com_a}")
        check("A: COM read while paused does NOT complete (main thread blocked)",
              com_a.get("status") == "timed_out"
              and com_a.get("worker_exit_confirmed") is True, com_a)

        # ================= B. DAP/LISP read of the drawing ====================
        # The drawing must be readable from the paused LISP context itself.
        lisp_reads = {
            "circle_count_via_ssget": '(sslength (ssget "X" (list (cons 0 "CIRCLE"))))',
            "last_entity_dxf": '(entget (entlast))',
            "circle_radius_via_entget": '(cdr (assoc 40 (entget (entlast))))',
            "circle_center_via_entget": '(cdr (assoc 10 (entget (entlast))))',
        }
        R["findings"]["B_dap_lisp_reads"] = {}
        for label, expr in lisp_reads.items():
            rr = c.call("evaluate", {"expression": expr, "context": "repl"}, timeout=args.timeout)
            body = (rr or {}).get("body")
            R["findings"]["B_dap_lisp_reads"][label] = {
                "expression": expr, "success": (rr or {}).get("success"),
                "result": (body or {}).get("result"), "message": (rr or {}).get("message")}
            log(f"      [B] {label}: success={(rr or {}).get('success')} result={(body or {}).get('result')!r}")
        got_count = R["findings"]["B_dap_lisp_reads"]["circle_count_via_ssget"]["result"]
        got_r = R["findings"]["B_dap_lisp_reads"]["circle_radius_via_entget"]["result"]
        check("B: paused LISP can read the drawing (circle count)", got_count not in (None, ""), got_count)
        check("B: paused LISP reports the circle radius as 3", str(got_r).strip() == "3", got_r)

        # ================= C. write attempt while paused ======================
        write_expr = ('(entmakex (list (cons 0 "CIRCLE") (cons 10 (list 0.0 0.0 0.0)) (cons 40 99.0)))')
        rr = c.call("evaluate", {"expression": write_expr, "context": "repl"}, timeout=args.timeout)
        R["findings"]["C_write_while_paused"] = {
            "expression": write_expr, "success": (rr or {}).get("success"),
            "result": ((rr or {}).get("body") or {}).get("result"),
            "message": (rr or {}).get("message")}
        log(f"      [C] write while paused -> success={(rr or {}).get('success')} "
            f"result={R['findings']['C_write_while_paused']['result']!r} "
            f"msg={R['findings']['C_write_while_paused']['message']!r}")

        # ================= D. second concurrent client ========================
        tpath2 = str(pathlib.Path(args.transcript).with_suffix(".second.jsonl"))
        if os.path.exists(tpath2):
            os.remove(tpath2)
        log("      [D] opening a SECOND DAP client against the same adapter")
        c2 = dap.DapClient([args.adapter], tpath2, timeout=20.0)
        try:
            r2 = c2.call("initialize", {"clientID": "cadbridge-second", "clientName": "second",
                                        "adapterID": "autolisp", "pathFormat": "path",
                                        "linesStartAt1": True, "columnsStartAt1": True},
                         timeout=15.0)
            R["findings"]["D_second_client"] = {
                "initialize": None if r2 is None else {"success": r2.get("success"),
                                                       "body": r2.get("body")}}
            log(f"      [D] second client initialize -> {R['findings']['D_second_client']}")
        finally:
            c2.close()

        # ================= E. step/continue still work ========================
        r = c.call("next", {"threadId": tid}, timeout=args.timeout)
        check("E: next accepted while paused", bool(r and r.get("success")),
              (r or {}).get("message"))
        ev_n = wait_stopped(c, 40)
        check("E: stopped again after next", ev_n is not None, (ev_n or {}).get("body"))
        if ev_n is not None:
            rr = c.call("stackTrace", {"threadId": tid})
            fr = ((rr or {}).get("body") or {}).get("stackFrames") or []
            R["findings"]["E_frames_after_next"] = fr

        r = c.call("continue", {"threadId": tid}, timeout=args.timeout)
        check("E: continue accepted", bool(r and r.get("success")), (r or {}).get("message"))
        time.sleep(6)
        R["findings"]["output_after_continue"] = drain_output(c)
        check("E: CBDBG_DONE printed after continue",
              any("CBDBG_DONE" in o for o in R["findings"]["output_after_continue"]),
              R["findings"]["output_after_continue"])

        # ================= F. stale frame handle ==============================
        if frame_id:
            r = c.call("scopes", {"frameId": frame_id}, timeout=15)
            R["findings"]["F_stale_frame"] = {"frameId": frame_id, "response": r}
            log(f"      [F] scopes on old frameId={frame_id} -> "
                f"success={(r or {}).get('success')} msg={(r or {}).get('message')!r}")

        # ================= G. COM read after continue =========================
        log(f"  [G] COM read AFTER continue (bounded {args.com_timeout:.0f}s)")
        com_g = bw.run_owned_com_read(owned_token, args.progid, args.com_timeout)
        snap_g = com_g.get("result") or {}
        R["findings"]["G_com_read_after_continue"] = com_g
        log(f"      worker={com_g}")
        check("G: COM read after continue completes",
              com_g.get("status") == "completed" and snap_g.get("status") == "ok", com_g)
        circ = (snap_g or {}).get("circles") or []
        check("G: the r=3 circle exists in the drawing",
              any(abs(x.get("radius", 0) - 3.0) < 1e-9 for x in circ), circ)
        check("G: the r=99 write was NOT applied (write refused while paused)",
              not any(abs(x.get("radius", 0) - 99.0) < 1e-9 for x in circ), circ)

        # --------- verdict ---------
        a_blocked = R["findings"]["A_com_read_while_paused"].get("status") == "timed_out"
        b_reads = got_count not in (None, "") and str(got_r).strip() == "3"
        R["verdict"] = {
            "main_thread_blocked_while_paused": a_blocked,
            "live_read_possible_via_dap_lisp_channel": b_reads,
            "live_read_possible_via_com_or_commandmethod": not a_blocked,
            "summary": ("Main thread is blocked while paused, so Plugin CommandMethod and COM "
                        "cannot serve a live query; the drawing IS readable from the paused "
                        "LISP context over the DAP channel."
                        if (a_blocked and b_reads) else
                        ("Main thread is blocked and the paused LISP context could not read "
                         "the drawing; T01-5 needs an owner decision."
                         if a_blocked else
                         "Main thread was NOT blocked; re-examine the earlier timeout.")),
        }
        log(f"\nVERDICT: {json.dumps(R['verdict'], indent=2, ensure_ascii=False)}")
        return 0
    finally:
        c.close()
        R["adapter_stderr"] = c.stderr_lines[:30]
        log(f"terminating only our CAD pid={pid}")
        # Verified-ownership termination (pid + creation time + exe path). Refuses if the
        # recorded identity no longer matches, e.g. after Windows reused the pid.
        sp.terminate_owned(owned_token, log=log)   # token only; a dict cannot authorize a kill
        pathlib.Path(args.out).write_text(json.dumps(R, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
        ok = sum(1 for x in R["checks"] if x["ok"])
        bad = sum(1 for x in R["checks"] if not x["ok"])
        log(f"\nchecks: {ok} passed, {bad} failed")
        log(f"results: {args.out}")


if __name__ == "__main__":
    raise SystemExit(main())
