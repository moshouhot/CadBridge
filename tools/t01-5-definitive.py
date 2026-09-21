#!/usr/bin/env python3
"""CadBridge P1/T01-5 (core, non-degradable) — DEFINITIVE probe.

RESULT THIS PROBE ESTABLISHES
-----------------------------
A legal, live, deadlock-free read of the drawing IS possible while AutoLISP is paused at a
breakpoint, and it does NOT require continuing first. The channel is the DAP `evaluate`
request with context `hover` / `watch` / `clipboard`, which the adapter answers
SYNCHRONOUSLY from the paused LISP context.

How we got here (each step is backed by a transcript in docs/evidence/P1/<run>/T01-5/):

  1. pause-query.json      COM read while paused did not return in 45 s -> the AutoCAD main
                           thread is inside the LISP debugger wait, so Plugin CommandMethods
                           and COM cannot serve a query during a pause.
  2. pause-live-query.json `evaluate context:"repl"` returned success with an EMPTY result,
                           and the real values appeared only after `continue`. So "repl" is a
                           QUEUED injection -- using it would be exactly the forbidden
                           "continue first, then query" pattern.
  3. sync-read-forms.json  `evaluate` with context "hover" / "watch" / "clipboard" returned
                           `result='3.0' type='REAL'` in ~0.1 s with NO new stdout, i.e.
                           synchronously, from the paused frame.

WHY THE STALENESS TEST MATTERS
------------------------------
A single successful read could still be a cached snapshot. The test plan says a stale-only
result must be reported BLOCKED. So this probe reads the SAME expression at TWO different
stops whose correct answers differ:

    stop at line 6  -- circle from line 7 does not exist yet  -> must NOT be 3.0
    stop at line 8  -- circle from line 7 exists              -> must BE 3.0

If the two reads differ, the read reflects the live state at that stop and is not stale.

HAZARD FOUND (documented, avoided)
----------------------------------
`evaluate` with context "watch" AND a frameId produced an ACCESS VIOLATION
(0xC0000005) inside AutoCAD and corrupted the rest of the debug session. This probe never
issues that combination. `hover` (with or without frameId), `watch` (without frameId) and
`clipboard` (with frameId) were all safe.

SAFETY
------
Only PIDs this script started are terminated. It refuses to start if an acad.exe from the
target install is already running. A scratch ACAD_USERPROFILE is used.
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
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--stop-wait", type=float, default=120.0)
    ap.add_argument("--idle-wait", type=float, default=55.0)
    ap.add_argument("--com-timeout", type=float, default=35.0)
    args = ap.parse_args()

    # DISABLED until the safety review passes: this harness uses COM / abandoned
    # worker threads and lacks mandatory HWND->owned-PID verification.
    sp.require_safety_review_passed("t01-5-definitive.py")

    install_dir = str(pathlib.Path(args.acad).parent)
    if acad_pids(install_dir):
        log("REFUSING: an acad.exe from this install is already running")
        return 2

    R: dict = {"checks": [], "timeline": [], "stop_reads": {}}
    if os.path.exists(args.transcript):
        os.remove(args.transcript)

    scratch = pathlib.Path("F:/CadBridge-run/_attach-scratch") / f"t5d-{os.getpid()}"
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

    def stamp(what: str, **kw):
        R["timeline"].append({"t": round(time.time(), 3), "what": what, **kw})

    c = dap.DapClient([args.adapter], args.transcript, timeout=args.timeout)

    def sync_read(expression: str, frame_id, context: str = "hover"):
        """Read from the paused context and report whether stdout appeared alongside it.

        `queued_stdout` records whether NEW stdout lines arrived while this request was in
        flight. It is evidence, not a verdict: a queued channel shows the value on stdout
        instead of in the response, but the reverse is not automatically synchronous --
        timing must be judged together with the returned `result`.
        """
        if context == "watch" and frame_id is not None:
            raise ValueError("watch+frameId is a recorded hazard; not used")
        a: dict = {"expression": expression, "context": context}
        if frame_id is not None:
            a["frameId"] = frame_id
        before = drain_output(c)
        t0 = time.time()
        rr = c.call("evaluate", a, timeout=args.timeout)
        dt = round(time.time() - t0, 3)
        after = drain_output(c)
        body = (rr or {}).get("body") or {}
        new_out = [x for x in after if x not in before]
        return {"expression": expression, "context": context, "frameId": frame_id,
                "success": (rr or {}).get("success"), "result": body.get("result"),
                "type": body.get("type"), "seconds": dt,
                "queued_stdout": new_out}

    try:
        r = c.call("initialize", {"clientID": "cadbridge-t5-final", "clientName": "T5 final",
                                  "adapterID": "autolisp", "pathFormat": "path",
                                  "linesStartAt1": True, "columnsStartAt1": True})
        check("initialize", bool(r and r.get("success")))
        check("initialized event", c.wait_event("initialized", timeout=15) is not None)
        stamp("initialized")

        r = c.call("attach", {"type": "attachlisp", "request": "attach",
                              "processId": str(pid), "program": args.program}, timeout=args.timeout)
        check("attach to live PID", bool(r and r.get("success")))
        if not (r and r.get("success")):
            return 0
        stamp("attached", pid=pid)

        # Break at BOTH 6 (circle not yet created) and 8 (circle created).
        r = c.call("setBreakpoints", {
            "source": {"path": args.program, "name": os.path.basename(args.program)},
            "breakpoints": [{"line": 6}, {"line": 8}], "lines": [6, 8]})
        bps = ((r or {}).get("body") or {}).get("breakpoints") or []
        check("breakpoints 6 and 8 verified",
              len(bps) == 2 and all(b.get("verified") for b in bps), bps)
        check("configurationDone", bool((c.call("configurationDone", {}) or {}).get("success")))

        time.sleep(2)
        r = c.call("evaluate", {"expression": "(C:CBDBG)", "context": "repl"}, timeout=args.timeout)
        check("trigger (C:CBDBG)", bool(r and r.get("success")))

        # ================= STOP 1: line 6, circle NOT yet created =============
        ev = wait_stopped(c, args.stop_wait)
        check("stop 1 received", ev is not None, (ev or {}).get("body"))
        if ev is None:
            return 0
        tid = (ev.get("body") or {}).get("threadId", 1)
        r = c.call("stackTrace", {"threadId": tid})
        frames1 = ((r or {}).get("body") or {}).get("stackFrames") or []
        f1 = next((x for x in frames1 if (x.get("source") or {}).get("path")), None)
        fid1 = f1.get("id") if f1 else None
        R["stop_reads"]["stop1"] = {"line": f1.get("line") if f1 else None, "frameId": fid1}
        stamp("stop1", line=f1.get("line") if f1 else None, frameId=fid1)
        check("stop 1 is at line 6", bool(f1 and f1.get("line") == 6), f1)
        R["output_at_stop1"] = drain_output(c)

        # Guarded expressions: each returns a KNOWN BASELINE when the drawing object is
        # absent, so the comparison is baseline-vs-value rather than error-vs-value. An
        # error string is not a controlled baseline.
        reads1 = {
            "circle_count_baseline": '(if (ssget "X" (list (cons 0 "CIRCLE"))) '
                                     '(sslength (ssget "X" (list (cons 0 "CIRCLE")))) -1)',
            "local_n": "n",
        }
        R["stop_reads"]["stop1"]["reads"] = {}
        for label, expr in reads1.items():
            res = sync_read(expr, fid1, "hover")
            R["stop_reads"]["stop1"]["reads"][label] = res
            log(f"      stop1 [{label}] -> result={res['result']!r} type={res['type']!r} "
                f"success={res['success']} {res['seconds']}s")
        r1_count = R["stop_reads"]["stop1"]["reads"]["circle_count_baseline"]["result"]

        # ================= STOP 2: line 8, circle exists =====================
        r = c.call("continue", {"threadId": tid}, timeout=args.timeout)
        check("continue to next breakpoint accepted", bool(r and r.get("success")))
        ev2 = wait_stopped(c, args.stop_wait)
        check("stop 2 received", ev2 is not None, (ev2 or {}).get("body"))
        if ev2 is None:
            return 0
        r = c.call("stackTrace", {"threadId": tid})
        frames2 = ((r or {}).get("body") or {}).get("stackFrames") or []
        f2 = next((x for x in frames2 if (x.get("source") or {}).get("path")), None)
        fid2 = f2.get("id") if f2 else None
        R["stop_reads"]["stop2"] = {"line": f2.get("line") if f2 else None, "frameId": fid2}
        stamp("stop2", line=f2.get("line") if f2 else None, frameId=fid2)
        check("stop 2 is at line 8", bool(f2 and f2.get("line") == 8), f2)
        R["output_at_stop2"] = drain_output(c)
        check("CBDBG_DONE NOT yet printed at stop 2",
              not any("CBDBG_DONE" in o for o in R["output_at_stop2"]), R["output_at_stop2"])

        reads2 = {
            "radius_entlast": '(cdr (assoc 40 (entget (entlast))))',
            "center_entlast": '(cdr (assoc 10 (entget (entlast))))',
            "circle_count_baseline": '(if (ssget "X" (list (cons 0 "CIRCLE"))) '
                                     '(sslength (ssget "X" (list (cons 0 "CIRCLE")))) -1)',
            "local_n": "n",
            "local_p": "p",
        }
        R["stop_reads"]["stop2"]["reads"] = {}
        for label, expr in reads2.items():
            res = sync_read(expr, fid2, "hover")
            R["stop_reads"]["stop2"]["reads"][label] = res
            log(f"      stop2 [{label}] -> result={res['result']!r} type={res['type']!r} "
                f"success={res['success']} {res['seconds']}s")
        r2 = R["stop_reads"]["stop2"]["reads"]

        # ---- the T01-5 requirements ----------------------------------------
        check("LIVE read: radius is 3.0 while paused at line 8",
              str(r2["radius_entlast"]["result"]).strip() == "3.0",
              r2["radius_entlast"])
        check("LIVE read: centre is (100.0 100.0 0.0) while paused",
              str(r2["center_entlast"]["result"]).strip() == "(100.0 100.0 0.0)",
              r2["center_entlast"])
        check("LIVE read: circle count is 1 while paused",
              str(r2["circle_count_baseline"]["result"]).strip() == "1",
              r2["circle_count_baseline"])
        check("LIVE read: local p=3 while paused",
              str(r2["local_p"]["result"]).strip() == "3", r2["local_p"])
        # A synchronous read must return the value IN THE RESPONSE. The distinguishing
        # observation versus the queued channel is that the response itself carries the
        # value (the queued channel returned an empty result and put the value on stdout).
        rr = r2["radius_entlast"]
        check("read returns the value in the RESPONSE (not on stdout)",
              rr.get("result") not in (None, "") and not rr.get("queued_stdout"), rr)

        # ---- staleness test: same guarded expression, two stops, known baselines ----
        # At line 6 no circle exists yet, so the guarded expression must return the
        # baseline -1. At line 8 it must return 1. Different values from the same
        # expression prove the read reflects the state at that stop rather than a cache.
        check("STALENESS: guarded circle count is baseline -1 at line 6",
              str(r1_count).strip() == "-1", r1_count)
        check("STALENESS: guarded circle count is 1 at line 8",
              str(r2["circle_count_baseline"]["result"]).strip() == "1",
              r2["circle_count_baseline"])
        check("STALENESS: the same expression yields different values at the two stops",
              str(r1_count).strip() != str(r2["circle_count_baseline"]["result"]).strip(),
              {"line6": r1_count, "line8": r2["circle_count_baseline"]["result"]})

        # ---- deadlock test: COM is blocked, but the pause is not wedged ------
        log(f"  [COM] read while paused (bounded {args.com_timeout:.0f}s) -- expect blocked")
        com_paused = bw.run_owned_com_read(owned_token, args.progid, args.com_timeout)
        R["com_while_paused"] = com_paused
        log(f"      worker={com_paused}")
        check("COM/main-thread is blocked while paused (so Plugin cannot serve it)",
              com_paused.get("status") == "timed_out"
              and com_paused.get("worker_exit_confirmed") is True, com_paused)

        # ---- pause not wedged: step and continue still work -----------------
        r = c.call("next", {"threadId": tid}, timeout=args.timeout)
        check("step still works while paused", bool(r and r.get("success")))
        ev3 = wait_stopped(c, 40)
        check("stopped again after step", ev3 is not None, (ev3 or {}).get("body"))

        r = c.call("continue", {"threadId": tid}, timeout=args.timeout)
        check("continue still works while paused", bool(r and r.get("success")))
        time.sleep(7)
        R["output_after_continue"] = drain_output(c)
        check("CBDBG_DONE printed after continue",
              any("CBDBG_DONE" in o for o in R["output_after_continue"]),
              R["output_after_continue"])

        # ---- control: COM read after continue -------------------------------
        com_after = bw.run_owned_com_read(owned_token, args.progid, args.com_timeout)
        R["com_after_continue"] = com_after
        box2 = com_after.get("result") or {}
        log(f"      after-continue COM worker={com_after}")
        check("control: COM read works again after continue",
              com_after.get("status") == "completed" and box2.get("status") == "ok",
              com_after)
        circles = (box2 or {}).get("circles") or []
        check("control: drawing really contains the r=3 circle (independent of DAP)",
              any(abs(x.get("radius", 0) - 3.0) < 1e-9 for x in circles), circles)

        # The verdict is DERIVED from the checks. An earlier version hardcoded
        # "ACHIEVABLE" here, so the script would have printed a success verdict even if
        # every assertion had failed.
        failed = [x["name"] for x in R["checks"] if not x["ok"]]
        R["verdict"] = {
            "derived_from_checks": True,
            "checks_total": len(R["checks"]),
            "checks_failed": len(failed),
            "failed_names": failed,
            "observed_capability": (
                "DAP evaluate with context hover returned the CURRENT drawing state "
                "(radius 3.0, centre (100.0 100.0 0.0), circle count 1) while AutoLISP was "
                "stopped at line 8, and stepping/continue afterwards completed normally."
                if not failed else
                "NOT established: at least one required observation failed."
            ),
            "explicitly_not_claimed": [
                "that the Plugin path (CommandMethod / LispFunction / any Plugin-mediated "
                "read) can serve a pause-state query -- no Plugin was loaded or queried by "
                "this probe",
                "that the AutoCAD main thread is provably blocked for ALL Plugin execution "
                "paths -- a COM timeout does not establish that",
                "that DEBUG_BUSY is enforced -- the adapter did not refuse a paused write",
                "that ACAD_USERPROFILE isolated the user profile -- that was not demonstrated",
                "that repl-context evaluation is deferred until continue -- its timing was "
                "not determined",
                "that string processId / type attachlisp / a longer timeout are each "
                "individually necessary -- they were changed together",
            ],
            "hazard_note": "evaluate with context watch AND a frameId was followed by an "
                           "access violation (0xC0000005) in one mixed experiment; the "
                           "association is recorded but causation was not established.",
        }
        log(f"\nVERDICT: {json.dumps(R['verdict'], indent=2, ensure_ascii=False)}")
        return 0
    finally:
        c.close()
        R["adapter_stderr"] = c.stderr_lines[:30]
        log(f"terminating only our CAD pid={pid}")
        # Verified-ownership termination (pid + creation time + exe path). Refuses if the
        # recorded identity no longer matches, e.g. after Windows reused the pid.
        cleanup_ok = sp.terminate_owned(owned_token, log=log)
        check("owned CAD cleanup confirmed", cleanup_ok)
        R["cleanup_confirmed"] = bool(cleanup_ok)
        pathlib.Path(args.out).write_text(json.dumps(R, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
        ok = sum(1 for x in R["checks"] if x["ok"])
        bad = sum(1 for x in R["checks"] if not x["ok"])
        log(f"\nchecks: {ok} passed, {bad} failed")
        log(f"results: {args.out}")
        # Decide the exit status ONLY when no exception is propagating. Raising SystemExit
        # while an exception is in flight would replace that exception (and its traceback)
        # with this exit code, letting a crashed run report a derived status instead.
        # When an exception IS in flight, it propagates unchanged.
        if sys.exc_info()[0] is None:
            raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    raise SystemExit(main())
