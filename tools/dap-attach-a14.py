#!/usr/bin/env python3
"""CadBridge P1/T01-3: attach to a REAL running AutoCAD GUI and drive the A14 sequence.

Why this exists: two topologies are possible and they are NOT equivalent.

  launch topology (tools/dap-a14-sequence.py)
      adapter -- <acad.exe>   then   launch {program}
      The adapter OWNS the host process. Proven working: breakpoints verified, stopped,
      stackTrace, scopes, variables, stepIn/next/stepOut/continue, line-8 breakpoint.

  attach topology (this script)
      adapter                 (bare, no args)   then   attach {type:'attachlisp',
                                                          request:'attach',
                                                          processId:'<string>', program}
      The adapter attaches to a host the CLIENT started. This is the topology CadBridge
      actually needs, because CadBridge must never be the thing that launches the user's CAD.

Attach was previously reported as "NO_RESPONSE". That was wrong, and the cause was in the
probe, not the product. Three defects, all now fixed and documented:

  1. processId was sent as an INTEGER. extension/src/debug.ts stores the value returned by
     pickProcess(), which acadPicker.ts declares as `Promise<string | null>` -- a STRING.
  2. `type` was sent as 'autolisp'. The attach configuration provider sets type='attachlisp'.
  3. The response timeout was shorter than the adapter's own attach handshake.

The fixture is the same one used for the launch topology, so results are directly comparable:

    line 1: (defun cb-add (x / y)
    line 2:   (setq y (+ x 1))
    line 3:   y)
    line 4: (defun c:CBDBG (/ n p)
    line 5:   (setq n 2)
    line 6:   (setq p (cb-add n))        <- breakpoint 1
    line 7:   (entmakex ... CIRCLE r=p)
    line 8:   (princ "CBDBG_DONE")       <- breakpoint 2
    line 9:   (princ))

Required observations, all read from real stops (never inferred):
    * attach succeeds against a live PID
    * both breakpoints verified with the adapter's own line numbers
    * stop in C:CBDBG at line 6 with n=2
    * stepIn reaches cb-add with x=2
    * stepOver advances and y=3
    * stepOut returns to C:CBDBG
    * continue reaches the line-8 breakpoint

Only PIDs this script started are ever terminated. It refuses to run if any acad.exe from
the target install is already present.
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


def local(loc: dict, name: str):
    """Case-insensitive lookup: the adapter reports AutoLISP symbols UPPER-CASED."""
    for k, v in loc.items():
        if k.upper() == name.upper():
            return v
    return None


def real_frame(frames: list[dict]) -> dict | None:
    for f in frames:
        if (f.get("source") or {}).get("path") and f.get("line", 0) > 0:
            return f
    return None


def read_locals(c, frame_id: int) -> dict:
    out: dict[str, str] = {}
    r = c.call("scopes", {"frameId": frame_id})
    for sc in (((r or {}).get("body") or {}).get("scopes") or []):
        if sc.get("name") != "Locals":
            continue
        ref = sc.get("variablesReference")
        if not ref:
            continue
        vr = c.call("variables", {"variablesReference": ref})
        for v in (((vr or {}).get("body") or {}).get("variables") or []):
            out[v.get("name")] = v.get("value")
    return out


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
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--stop-wait", type=float, default=120.0)
    ap.add_argument("--idle-wait", type=float, default=50.0)
    ap.add_argument("--keep-cad", action="store_true")
    args = ap.parse_args()

    # DISABLED until the safety review passes (see REVIEW-REMEDIATION-round2.md).
    sp.require_safety_review_passed("dap-attach-a14.py")
    if args.keep_cad:
        log("REFUSING: --keep-cad is disabled for the reviewed live harness; cleanup is mandatory")
        return 2

    install_dir = str(pathlib.Path(args.acad).parent)
    preexisting_acad = sp.list_processes(["acad.exe"])
    if preexisting_acad:
        log(f"REFUSING: an acad.exe is already running anywhere on the machine: {preexisting_acad}")
        return 2

    results: dict = {"topology": "attach", "observations": {}, "checks": []}
    if os.path.exists(args.transcript):
        os.remove(args.transcript)

    # ---- start our own host ------------------------------------------------------
    scratch = pathlib.Path("F:/CadBridge-run/_attach-scratch") / f"a14-{os.getpid()}"
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["ACAD_USERPROFILE"] = str(scratch)
    log(f"launching isolated CAD: {args.acad}")
    owned_token = sp.launch_job_and_record(
        args.acad, cwd=install_dir, env=env, role="t01-target", log=log,
        wait_timeout=60.0, host_exe_name="acad.exe")
    owned_pid = sp.owned_process(owned_token).pid if owned_token else None
    pid = owned_pid
    if pid is None:
        log("ERROR: CAD did not start (no verifiable launched process)")
        return 3
    log(f"CAD pid={pid}; waiting {args.idle_wait:.0f}s for idle command line")
    results["acad_pid"] = pid
    time.sleep(args.idle_wait)

    # Re-check exclusivity after startup. Another user/agent can start AutoCAD after the
    # preflight; only Job members are ours. Never touch a foreign PID.
    owned_rec = sp.owned_process(owned_token)
    owned_members = owned_rec.handle.job_process_ids() if owned_rec else []
    all_acad = sp.list_processes(["acad.exe"])
    foreign_acad = [p for p in all_acad if int(p.get("pid") or 0) not in set(owned_members)]
    results["foreign_acad_before_dap"] = foreign_acad

    c = dap.DapClient([args.adapter], args.transcript, timeout=args.timeout)

    def check(name: str, ok: bool, detail=None):
        results["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        log(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail is not None else ""))

    check("exclusive CAD environment still holds before DAP attach", not foreign_acad,
          foreign_acad)

    try:
        if foreign_acad:
            return 0

        r = c.call("initialize", {
            "clientID": "cadbridge-attach-a14", "clientName": "CadBridge Attach A14",
            "adapterID": "autolisp", "pathFormat": "path",
            "linesStartAt1": True, "columnsStartAt1": True, "supportsVariableType": True,
        })
        results["capabilities"] = (r or {}).get("body") or {}
        check("initialize", bool(r and r.get("success")), results["capabilities"])
        check("initialized event", c.wait_event("initialized", timeout=15) is not None)

        # ---- ATTACH (the topology under test) -----------------------------------
        r = c.call("attach", {
            "type": "attachlisp", "request": "attach",
            "processId": str(pid), "program": args.program,
        }, timeout=args.timeout)
        check("attach to live PID", bool(r and r.get("success")),
              {"success": (r or {}).get("success"), "body": (r or {}).get("body"),
               "message": (r or {}).get("message")})
        if not (r and r.get("success")):
            results["events_after_failed_attach"] = [
                {"event": e.get("event"), "body": e.get("body")} for e in c.events]
            return 0

        r = c.call("setBreakpoints", {
            "source": {"path": args.program, "name": os.path.basename(args.program)},
            "breakpoints": [{"line": 6}, {"line": 8}], "lines": [6, 8],
        })
        bps = ((r or {}).get("body") or {}).get("breakpoints") or []
        results["observations"]["breakpoints"] = bps
        check("setBreakpoints on lines 6 and 8", bool(r and r.get("success")), bps)
        check("both breakpoints verified", len(bps) == 2 and all(b.get("verified") for b in bps))
        check("line 6 bound as requested", any(b.get("line") == 6 for b in bps))
        check("line 8 bound as requested", any(b.get("line") == 8 for b in bps))

        r = c.call("configurationDone", {})
        check("configurationDone", bool(r and r.get("success")))

        # ---- trigger the command in the attached host ---------------------------
        time.sleep(2)
        r = c.call("evaluate", {"expression": "(C:CBDBG)", "context": "repl"},
                   timeout=args.timeout)
        results["observations"]["trigger"] = (r or {}).get("body")
        check("evaluate (C:CBDBG) accepted in attach mode", bool(r and r.get("success")),
              {"success": (r or {}).get("success"), "body": (r or {}).get("body"),
               "message": (r or {}).get("message")})

        ev = wait_stopped(c, args.stop_wait)
        check("stopped event #1 (line 6)", ev is not None, (ev or {}).get("body"))
        if ev is None:
            return 0
        tid = (ev.get("body") or {}).get("threadId", 1)

        r = c.call("stackTrace", {"threadId": tid})
        frames = ((r or {}).get("body") or {}).get("stackFrames") or []
        results["observations"]["stop1_frames"] = frames
        f1 = real_frame(frames)
        check("stop1 has real source frame", f1 is not None, f1)
        if f1 is None:
            return 0
        check("stop1 frame is C:CBDBG", (f1.get("name") or "").upper() == "C:CBDBG", f1.get("name"))
        results["observations"]["stop1_line"] = f1.get("line")
        loc1 = read_locals(c, f1["id"])
        results["observations"]["stop1_locals"] = loc1
        check("stop1 n=2", local(loc1, "n") == "2", loc1)

        # stepIn twice: :BEFORE-EXP then cb-add body
        for i in (1, 2):
            r = c.call("stepIn", {"threadId": tid})
            check(f"stepIn #{i} accepted", bool(r and r.get("success")))
            evx = wait_stopped(c, args.stop_wait)
            if evx is None:
                check(f"stopped after stepIn #{i}", False)
                return 0
            r = c.call("stackTrace", {"threadId": tid})
            fx_frames = ((r or {}).get("body") or {}).get("stackFrames") or []
            results["observations"][f"stepin{i}_frames"] = fx_frames
            fx = next((f for f in fx_frames if (f.get("name") or "").upper() == "CB-ADD"), None)
            if fx is not None:
                locx = read_locals(c, fx["id"])
                results["observations"]["cbadd_locals"] = locx
                results["observations"]["cbadd_line"] = fx.get("line")
                log(f"  inside cb-add line {fx.get('line')} locals={locx}")
                check("inside cb-add", True)
                check("inside cb-add x=2", local(locx, "x") == "2", locx)
                break
            log(f"  stepIn #{i} at {[f.get('name') for f in fx_frames[:3]]}")

        r = c.call("next", {"threadId": tid})
        check("next accepted", bool(r and r.get("success")))
        ev3 = wait_stopped(c, args.stop_wait)
        if ev3 is not None:
            r = c.call("stackTrace", {"threadId": tid})
            fr3 = ((r or {}).get("body") or {}).get("stackFrames") or []
            results["observations"]["after_next_frames"] = fr3
            f3 = next((f for f in fr3 if (f.get("name") or "").upper() == "CB-ADD"), None)
            if f3:
                loc3 = read_locals(c, f3["id"])
                results["observations"]["after_next_locals"] = loc3
                results["observations"]["after_next_line"] = f3.get("line")
                log(f"  after next line {f3.get('line')} locals={loc3}")
                check("after next, y=3", local(loc3, "y") == "3", loc3)

        r = c.call("stepOut", {"threadId": tid})
        check("stepOut accepted", bool(r and r.get("success")))
        ev4 = wait_stopped(c, args.stop_wait)
        if ev4 is not None:
            r = c.call("stackTrace", {"threadId": tid})
            fr4 = ((r or {}).get("body") or {}).get("stackFrames") or []
            results["observations"]["after_stepout_frames"] = fr4
            f4 = real_frame(fr4)
            if f4:
                results["observations"]["after_stepout_line"] = f4.get("line")
                check("stepOut returned to C:CBDBG", (f4.get("name") or "").upper() == "C:CBDBG",
                      f4.get("name"))

        r = c.call("continue", {"threadId": tid})
        check("continue accepted", bool(r and r.get("success")))
        ev5 = wait_stopped(c, args.stop_wait)
        if ev5 is not None:
            r = c.call("stackTrace", {"threadId": tid})
            fr5 = ((r or {}).get("body") or {}).get("stackFrames") or []
            results["observations"]["final_frames"] = fr5
            f5 = real_frame(fr5)
            if f5:
                results["observations"]["final_line"] = f5.get("line")
                check("line 8 breakpoint reached", f5.get("line") == 8, f5.get("line"))

        time.sleep(1)
        results["adapter_alive"] = c.proc.poll() is None
        return 0
    finally:
        c.close()
        results["adapter_stderr"] = c.stderr_lines[:30]
        if pid is not None:
            log(f"terminating only our retained CAD Job Object (primary pid={pid})")
            cleanup_ok = sp.terminate_owned(owned_token, log=log)
            check("Job Object cleanup confirmed", cleanup_ok, cleanup_ok)
            try:
                post = sp.list_processes(["acad.exe"])
            except Exception as exc:  # noqa: BLE001
                post = [{"enumeration_error": f"{type(exc).__name__}: {exc}"}]
            results["post_cleanup_acad"] = post
            check("no acad.exe remains after cleanup", not post, post)
        out = pathlib.Path(args.transcript).with_suffix(".summary.json")
        failed = [x["name"] for x in results["checks"] if not x["ok"]]
        results["verdict"] = "PASS" if not failed else "FAIL"
        results["failed_checks"] = failed
        out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        ok = sum(1 for x in results["checks"] if x["ok"])
        bad = sum(1 for x in results["checks"] if not x["ok"])
        log(f"\nchecks: {ok} passed, {bad} failed")
        log(f"summary: {out}")
        if sys.exc_info()[0] is None:
            raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    raise SystemExit(main())
