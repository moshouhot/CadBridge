#!/usr/bin/env python3
"""CadBridge P1/T01-5: can the PLUGIN read the drawing while AutoLISP is paused?

This is the experiment the requirement actually asks for. ACCEPTANCE A15 / p1-test-plan
T01-5 require the read to go through the Plugin -- not through a plain-Lisp expression
evaluated over the raw DAP channel.

The distinction matters and is easy to miss:
    (entget (entlast))     is LISP running in the paused interpreter.
    (CBLIVEREAD)           is MANAGED PLUGIN CODE (CadBridge.Plugin.Shared
                           .LiveReadLispFunction) invoked from the paused interpreter.

Only the second is "Plugin-mediated". The function is registered with
[LispFunction("CBLIVEREAD")], is read-only (OpenMode.ForRead, no Commit), takes a
DocumentLock plus a read-only transaction, runs on the calling thread (never a background
thread), and embeds a marker string so the returned value cannot be confused with a
plain-Lisp result.

Sequence:
  1. Launch an isolated AutoCAD whose startup script NETLOADs the plugin (so CBLIVEREAD is
     registered). The script does NOT create the circle -- the debugger controls that.
  2. Attach the official DAP adapter to that running PID.
  3. Break at line 8 of the fixture (line 7 has created the circle; line 8 has not run).
  4. Trigger (C:CBDBG).
  5. WHILE PAUSED, evaluate (CBLIVEREAD) over DAP.
       - If it returns CBLIVEREAD_OK with circles=1 and the marker, a Plugin-mediated
         pause-state read works and T01-5's central mechanism is demonstrated.
       - If it times out or errors, that is the honest negative result.
  6. Also read the drawing with plain Lisp, to separate "Plugin code cannot run" from
     "nothing can read here".
  7. Continue and confirm the program completed, then verify independently over COM.

Safety: only the PID this script started is terminated, and only after re-verifying its
creation time and executable path (tools/safe_process.py). A scratch ACAD_USERPROFILE is
set; its isolation effect is NOT claimed.
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

sys.path.insert(0, str(_here))
import safe_process as sp  # noqa: E402
import bounded_worker as bw  # noqa: E402


def log(m: str) -> None:
    print(m, flush=True)


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


def eval_paused(c, expression: str, frame_id, timeout: float) -> dict:
    """Evaluate in the paused context and report the full outcome.

    Uses context "hover", which earlier measurement showed returns the value in the
    RESPONSE. context "watch" with a frameId is deliberately never used (recorded hazard).
    """
    a: dict = {"expression": expression, "context": "hover"}
    if frame_id is not None:
        a["frameId"] = frame_id
    before = drain_output(c)
    t0 = time.time()
    rr = c.call("evaluate", a, timeout=timeout)
    dt = round(time.time() - t0, 3)
    after = drain_output(c)
    body = (rr or {}).get("body") or {}
    return {
        "expression": expression,
        "returned": rr is not None,
        "success": (rr or {}).get("success"),
        "result": body.get("result"),
        "type": body.get("type"),
        "message": (rr or {}).get("message"),
        "seconds": dt,
        "new_stdout": [x for x in after if x not in before],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--acad", required=True)
    ap.add_argument("--program", required=True, help="the .lsp fixture")
    ap.add_argument("--plugin-dll", required=True, help="CadBridge plugin DLL to NETLOAD")
    ap.add_argument("--progid", default="AutoCAD.Application.25.1")
    ap.add_argument("--out", required=True)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--workdir", default="F:/CadBridge-run/_t01-5-plugin")
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("--stop-wait", type=float, default=150.0)
    ap.add_argument("--idle-wait", type=float, default=60.0)
    ap.add_argument("--plugin-timeout", type=float, default=60.0)
    args = ap.parse_args()

    # DISABLED until the safety review passes: this harness uses COM / abandoned
    # worker threads and lacks mandatory HWND->owned-PID verification.
    sp.require_safety_review_passed("t01-5-plugin-pause-read.py")

    install_dir = str(pathlib.Path(args.acad).parent)
    if sp.install_pids(install_dir, ["acad.exe"]):
        log("REFUSING: an acad.exe from this install is already running")
        return 2

    wd = pathlib.Path(args.workdir)
    wd.mkdir(parents=True, exist_ok=True)
    R: dict = {"checks": [], "paused_reads": {}}
    if os.path.exists(args.transcript):
        os.remove(args.transcript)

    # ---- startup script: NETLOAD the plugin, then keep the session alive -------
    # No circle is created here; the debugger's fixture does that. No QUIT: the host must
    # stay up so the adapter can attach.
    scr = wd / "netload-only.scr"
    scr.write_text(
        "NETLOAD\n"
        f"{args.plugin_dll}\n"
        '(princ "\\nCB_PLUGIN_LOADED\\n")\n'
        "(princ)\n",
        encoding="ascii", newline="\r\n")
    R["startup_script"] = str(scr)
    log(f"startup script: {scr}")

    scratch = pathlib.Path("F:/CadBridge-run/_attach-scratch") / f"t5p-{os.getpid()}"
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["ACAD_USERPROFILE"] = str(scratch)

    def check(name, ok, detail=None):
        R["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        log(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail is not None else ""))

    # ---- launch with the plugin loaded ----------------------------------------
    log(f"launching CAD with /b {scr.name}")
    owned_token = sp.launch_and_record(args.acad, args=["/b", str(scr)], cwd=install_dir,
                                 env=env, role="t01-5-plugin", log=log)
    if owned_token is None:
        log("ERROR: CAD did not start")
        return 3
    _rec = sp.owned_process(owned_token)
    pid = _rec.pid if _rec else None
    R["acad_pid"] = pid
    log(f"CAD pid={pid}; waiting {args.idle_wait:.0f}s for the script to finish and idle")
    time.sleep(args.idle_wait)

    c = dap.DapClient([args.adapter], args.transcript, timeout=args.timeout)
    try:
        r = c.call("initialize", {"clientID": "cadbridge-t5-plugin",
                                  "clientName": "CadBridge T5 plugin",
                                  "adapterID": "autolisp", "pathFormat": "path",
                                  "linesStartAt1": True, "columnsStartAt1": True})
        check("initialize", bool(r and r.get("success")))
        check("initialized event", c.wait_event("initialized", timeout=15) is not None)

        r = c.call("attach", {"type": "attachlisp", "request": "attach",
                              "processId": str(pid), "program": args.program},
                   timeout=args.timeout)
        check("attach to the CAD that has the plugin loaded", bool(r and r.get("success")),
              {"success": (r or {}).get("success"), "message": (r or {}).get("message")})
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
        fid = f.get("id") if f else None
        R["stop_frame"] = f
        check("stopped in a real source frame at line 8", bool(f and f.get("line") == 8), f)
        drain_output(c)

        # ================= THE EXPERIMENT ==================================
        # 1. Is our managed LispFunction even registered? (non-paused question, but asked here)
        # 2. Can PLUGIN code read the drawing while paused?
        log("  >>> evaluating (CBLIVEREAD) -- MANAGED PLUGIN CODE -- while paused")
        plugin_read = eval_paused(c, "(CBLIVEREAD)", fid, args.plugin_timeout)
        R["paused_reads"]["plugin_lispfunction"] = plugin_read
        log(f"      returned={plugin_read['returned']} success={plugin_read['success']} "
            f"result={plugin_read['result']!r} type={plugin_read['type']!r} "
            f"{plugin_read['seconds']}s")
        if plugin_read.get("new_stdout"):
            log(f"      new stdout: {plugin_read['new_stdout']}")

        # 3. Control: plain Lisp read in the same paused context, for contrast.
        log("  >>> evaluating plain-Lisp read while paused (control)")
        plain_read = eval_paused(c, '(cdr (assoc 40 (entget (entlast))))', fid, args.timeout)
        R["paused_reads"]["plain_lisp"] = plain_read
        log(f"      success={plain_read['success']} result={plain_read['result']!r} "
            f"type={plain_read['type']!r} {plain_read['seconds']}s")

        # ---- interpret -------------------------------------------------------
        marker = "src=CadBridge.Plugin.Shared.LiveReadLispFunction"
        pres = str(plugin_read.get("result") or "")
        plugin_ok = plugin_read.get("returned") and "CBLIVEREAD_OK" in pres and marker in pres
        R["plugin_read_verdict"] = {
            "plugin_code_ran_while_paused": plugin_ok,
            "returned_marker": marker in pres,
            "raw_result": plugin_read.get("result"),
            "note": ("CBLIVEREAD is managed plugin code registered with "
                     "[LispFunction]; the marker in its return value distinguishes it from "
                     "plain-Lisp evaluation."),
        }
        check("PLUGIN code (CBLIVEREAD) executed while paused", plugin_ok, plugin_read)
        check("PLUGIN read reported the circle created by line 7 (circles=1)",
              "circles=1" in pres, pres)
        check("PLUGIN read reported r=3 for the last circle", "r=3" in pres, pres)
        check("control: plain-Lisp read also works while paused",
              str(plain_read.get("result") or "").strip() == "3.0", plain_read)

        # ---- resume and confirm nothing was wedged --------------------------
        r = c.call("continue", {"threadId": tid}, timeout=args.timeout)
        check("continue accepted after the paused plugin read", bool(r and r.get("success")))
        time.sleep(7)
        R["output_after_continue"] = drain_output(c)
        check("CBDBG_DONE printed after continue",
              any("CBDBG_DONE" in o for o in R["output_after_continue"]),
              R["output_after_continue"])

        # ---- independent COM cross-check ------------------------------------
        log("  >>> independent COM cross-check after continue")
        com_after = bw.run_owned_com_read(owned_token, args.progid, 40)
        box = com_after.get("result") or {}
        R["com_after_continue"] = com_after
        log(f"      worker={com_after}")
        check("independent COM read confirms the r=3 circle",
              com_after.get("status") == "completed" and box.get("status") == "ok"
              and any(abs(x.get("radius", 0) - 3.0) < 1e-9 for x in (box.get("circles") or [])),
              com_after)

        # ---- derived verdict -------------------------------------------------
        failed = [x["name"] for x in R["checks"] if not x["ok"]]
        R["verdict"] = {
            "derived_from_checks": True,
            "checks_total": len(R["checks"]),
            "checks_failed": len(failed),
            "failed_names": failed,
            "plugin_mediated_pause_read": "DEMONSTRATED" if (plugin_ok and not failed)
                                            else "NOT DEMONSTRATED",
            "explicitly_not_claimed": [
                "that DEBUG_BUSY / STALE_DEBUG_HANDLE / control leases are implemented -- "
                "they are Host policy and remain unimplemented",
                "that ACAD_USERPROFILE isolates the user's real profile -- not demonstrated",
                "that writes during a pause are refused -- the raw adapter does not refuse them",
                "that this generalises to other AutoCAD versions -- only the host under test "
                "was exercised",
            ],
        }
        log(f"\nVERDICT: {json.dumps(R['verdict'], indent=2, ensure_ascii=False)}")
        return 0
    finally:
        c.close()
        R["adapter_stderr"] = c.stderr_lines[:30]
        log(f"terminating only our CAD pid={pid} (verified ownership)")
        sp.terminate_owned(owned_token, log=log)   # token only; a dict cannot authorize a kill
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
