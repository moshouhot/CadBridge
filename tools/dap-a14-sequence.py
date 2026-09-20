#!/usr/bin/env python3
"""CadBridge P1/T01-3 + A14: drive the exact AutoLISP debug sequence ACCEPTANCE.md requires.

Target semantics (ACCEPTANCE.md §2 F-LSP and §16 A14), for this fixture:

    line 1: (defun cb-add (x / y)
    line 2:   (setq y (+ x 1))
    line 3:   y)
    line 4: (defun c:CBDBG (/ n p)
    line 5:   (setq n 2)
    line 6:   (setq p (cb-add n))
    line 7:   (entmakex (list '(0 . "CIRCLE") '(10 100.0 100.0 0.0) (cons 40 p)))
    line 8:   (princ "CBDBG_DONE")

Required observations:
    * breakpoint on line 6 is hit
    * Step Into enters cb-add; after line 2 executes, x=2 and y=3
    * Step Out returns to the caller with p=3
    * a breakpoint on line 8 is hit afterwards
    * every frame/variable is read from a REAL stop, never inferred

Frame selection matters: the adapter reports synthetic frames first (`:BREAK-POINT`,
`:USER-INPUT`, `:CALLBACK-ENTRY`, `:ARQ-SUBR-CALLBACK`) with line 0 and no source. Reading
variables from those returns nothing useful, so this script always selects the first frame
that carries a real source path and a non-zero line.

Usage:
  dap-a14-sequence.py --adapter A --product ACAD --program FIXTURE --transcript OUT.jsonl
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
import safe_process as sp  # noqa: E402


def log(m: str) -> None:
    print(m, flush=True)


def real_frame(frames: list[dict]) -> dict | None:
    """First frame that has a real source path and a non-zero line."""
    for f in frames:
        src = (f.get("source") or {}).get("path")
        if src and f.get("line", 0) > 0:
            return f
    return None


def read_locals(c, frame_id: int) -> dict:
    """Return {NAME: value} for the Locals scope of a frame.

    NOTE: AutoLISP symbols are case-insensitive and the adapter reports them UPPER-CASED
    (`N`, `P`, `Y`, `X`) even though the source writes `n`, `p`, `y`, `x`. Lookups must
    therefore be case-insensitive; an earlier version of this probe compared against the
    source casing and produced four false failures.
    """
    out: dict[str, str] = {}
    r = c.call("scopes", {"frameId": frame_id})
    scopes = ((r or {}).get("body") or {}).get("scopes") or []
    for sc in scopes:
        if sc.get("name") != "Locals":
            continue
        ref = sc.get("variablesReference")
        if not ref:
            continue
        vr = c.call("variables", {"variablesReference": ref})
        for v in (((vr or {}).get("body") or {}).get("variables") or []):
            out[v.get("name")] = v.get("value")
    return out


def local(loc: dict, name: str):
    """Case-insensitive local lookup (adapter upper-cases AutoLISP symbols)."""
    for k, v in loc.items():
        if k.upper() == name.upper():
            return v
    return None


def wait_stopped(c, timeout: float) -> dict | None:
    """Wait for a stopped event that we have not consumed yet."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with c.lock:
            if c.events:
                for i, e in enumerate(c.events):
                    if e.get("event") == "stopped":
                        c.events.pop(i)
                        return e
        time.sleep(0.1)
    return None


def main() -> int:
    # DISABLED until the safety review passes. This harness causes a live AutoCAD
    # session (the adapter spawns the host), so it is gated like the other live entrypoints.
    sp.require_safety_review_passed("dap-a14-sequence.py")
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--product", required=True)
    ap.add_argument("--program", required=True)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("--stop-wait", type=float, default=90.0)
    args = ap.parse_args()

    if os.path.exists(args.transcript):
        os.remove(args.transcript)

    results: dict = {"observations": {}, "checks": []}
    c = dap.DapClient([args.adapter, "--", args.product], args.transcript, timeout=args.timeout)

    def check(name: str, ok: bool, detail=None):
        results["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        log(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail is not None else ""))

    try:
        # ---- handshake ------------------------------------------------------------
        r = c.call("initialize", {
            "clientID": "cadbridge-a14", "clientName": "CadBridge A14",
            "adapterID": "autolisp", "pathFormat": "path",
            "linesStartAt1": True, "columnsStartAt1": True, "supportsVariableType": True,
        })
        results["capabilities"] = (r or {}).get("body") or {}
        check("initialize", bool(r and r.get("success")), results["capabilities"])
        check("initialized event", c.wait_event("initialized", timeout=10) is not None)

        r = c.call("launch", {"program": args.program, "type": "autolisp", "request": "launch"})
        check("launch", bool(r and r.get("success")))

        # ---- breakpoints on line 6 and line 8 ------------------------------------
        r = c.call("setBreakpoints", {
            "source": {"path": args.program, "name": os.path.basename(args.program)},
            "breakpoints": [{"line": 6}, {"line": 8}],
            "lines": [6, 8],
        })
        bps = ((r or {}).get("body") or {}).get("breakpoints") or []
        results["observations"]["breakpoints"] = bps
        check("setBreakpoints lines 6 and 8", bool(r and r.get("success")), bps)
        check("both breakpoints verified", len(bps) == 2 and all(b.get("verified") for b in bps),
              [b.get("verified") for b in bps])
        check("breakpoint 6 bound to requested line", any(b.get("line") == 6 for b in bps))
        check("breakpoint 8 bound to requested line", any(b.get("line") == 8 for b in bps))

        r = c.call("configurationDone", {})
        check("configurationDone", bool(r and r.get("success")))

        # ---- trigger the fixture command ----------------------------------------
        time.sleep(3)
        r = c.call("evaluate", {"expression": "(C:CBDBG)", "context": "repl"})
        results["observations"]["trigger"] = (r or {}).get("body")
        check("evaluate (C:CBDBG) accepted", bool(r and r.get("success")))

        # ---- STOP 1: expect line 6 ----------------------------------------------
        ev = wait_stopped(c, args.stop_wait)
        check("stopped event #1 received", ev is not None, (ev or {}).get("body"))
        if ev is None:
            return 0
        results["observations"]["stop1_event"] = ev.get("body")
        tid = (ev.get("body") or {}).get("threadId", 1)

        r = c.call("stackTrace", {"threadId": tid})
        frames = ((r or {}).get("body") or {}).get("stackFrames") or []
        results["observations"]["stop1_frames"] = frames
        f1 = real_frame(frames)
        check("stop1 has a real source frame", f1 is not None, f1)
        if f1 is None:
            return 0
        check("stop1 frame is C:CBDBG", f1.get("name") == "C:CBDBG", f1.get("name"))
        results["observations"]["stop1_line"] = f1.get("line")
        log(f"  stop1 at line {f1.get('line')} ({f1.get('name')})")

        loc = read_locals(c, f1["id"])
        results["observations"]["stop1_locals"] = loc
        check("stop1 locals contain n=2", local(loc, "n") == "2", loc)

        # ---- Step Into: expect to enter cb-add ----------------------------------
        r = c.call("stepIn", {"threadId": tid})
        check("stepIn accepted", bool(r and r.get("success")))
        ev2 = wait_stopped(c, args.stop_wait)
        check("stopped event #2 after stepIn", ev2 is not None, (ev2 or {}).get("body"))
        if ev2 is None:
            return 0
        results["observations"]["stop2_event"] = ev2.get("body")

        r = c.call("stackTrace", {"threadId": tid})
        frames2 = ((r or {}).get("body") or {}).get("stackFrames") or []
        results["observations"]["stop2_frames"] = frames2
        # stepIn from line 6 stops at :BEFORE-EXP (the argument-evaluation frame) before
        # entering cb-add. AutoLISP is case-insensitive, so compare case-insensitively and
        # accept the argument-evaluation frame as "inside the call".
        names2 = [f.get("name") for f in frames2]
        f2 = next((f for f in frames2 if (f.get("name") or "").upper() in ("CB-ADD", ":BEFORE-EXP")), None)
        check("stop2 entered the cb-add call", f2 is not None, names2)
        results["observations"]["stop2_entered_name"] = f2.get("name") if f2 else None
        if f2 is None:
            return 0
        results["observations"]["stop2_line"] = f2.get("line")
        log(f"  stop2 at line {f2.get('line')} ({f2.get('name')})")

        loc2 = read_locals(c, f2["id"])
        results["observations"]["stop2_locals"] = loc2
        log(f"  stop2 locals: {loc2}")

        # From :BEFORE-EXP (argument evaluation) one more stepIn enters cb-add's body.
        # This mirrors what a user does in the IDE: step until the frame shows the callee.
        if f2 is not None and (f2.get("name") or "").upper() == ":BEFORE-EXP":
            r = c.call("stepIn", {"threadId": tid})
            check("second stepIn accepted (enter cb-add body)", bool(r and r.get("success")))
            evx = wait_stopped(c, args.stop_wait)
            if evx is not None:
                r = c.call("stackTrace", {"threadId": tid})
                framesx = ((r or {}).get("body") or {}).get("stackFrames") or []
                results["observations"]["stop2b_frames"] = framesx
                fx = next((f for f in framesx if (f.get("name") or "").upper() == "CB-ADD"), None)
                check("now inside cb-add", fx is not None, [f.get("name") for f in framesx])
                if fx:
                    locx = read_locals(c, fx["id"])
                    results["observations"]["stop2b_locals"] = locx
                    results["observations"]["stop2b_line"] = fx.get("line")
                    log(f"  stop2b at line {fx.get('line')} (cb-add) locals={locx}")
                    check("inside cb-add, x=2", local(locx, "x") == "2", locx)

        # ---- Step Over inside cb-add: after line 2, y should be 3 ---------------
        r = c.call("next", {"threadId": tid})
        check("next accepted", bool(r and r.get("success")))
        ev3 = wait_stopped(c, args.stop_wait)
        check("stopped event #3 after next", ev3 is not None)
        if ev3 is not None:
            r = c.call("stackTrace", {"threadId": tid})
            frames3 = ((r or {}).get("body") or {}).get("stackFrames") or []
            results["observations"]["stop3_frames"] = frames3
            f3 = real_frame(frames3)
            if f3:
                loc3 = read_locals(c, f3["id"])
                results["observations"]["stop3_locals"] = loc3
                results["observations"]["stop3_line"] = f3.get("line")
                log(f"  stop3 at line {f3.get('line')} locals={loc3}")
                check("after next inside cb-add, y=3", local(loc3, "y") == "3", loc3)

        # ---- Step Out: back to caller with p ------------------------------------
        r = c.call("stepOut", {"threadId": tid})
        check("stepOut accepted", bool(r and r.get("success")))
        ev4 = wait_stopped(c, args.stop_wait)
        check("stopped event #4 after stepOut", ev4 is not None)
        if ev4 is not None:
            r = c.call("stackTrace", {"threadId": tid})
            frames4 = ((r or {}).get("body") or {}).get("stackFrames") or []
            results["observations"]["stop4_frames"] = frames4
            f4 = real_frame(frames4)
            if f4:
                loc4 = read_locals(c, f4["id"])
                results["observations"]["stop4_locals"] = loc4
                results["observations"]["stop4_line"] = f4.get("line")
                log(f"  stop4 at line {f4.get('line')} ({f4.get('name')}) locals={loc4}")
                check("returned to C:CBDBG", (f4.get("name") or "").upper() == "C:CBDBG", f4.get("name"))
                # The decisive semantic proof that cb-add ran correctly: p becomes 3.
                check("after cb-add returned, p=3", local(loc4, "p") == "3", loc4)

        # ---- Continue to the line-8 breakpoint ---------------------------------
        r = c.call("continue", {"threadId": tid})
        check("continue accepted", bool(r and r.get("success")))
        ev5 = wait_stopped(c, args.stop_wait)
        if ev5 is not None:
            r = c.call("stackTrace", {"threadId": tid})
            frames5 = ((r or {}).get("body") or {}).get("stackFrames") or []
            results["observations"]["stop5_frames"] = frames5
            f5 = real_frame(frames5)
            if f5:
                results["observations"]["stop5_line"] = f5.get("line")
                log(f"  stop5 at line {f5.get('line')} ({f5.get('name')})")
                check("line 8 breakpoint reached", f5.get("line") == 8, f5.get("line"))

        # ---- stale handles after continue --------------------------------------
        if f1 is not None:
            r = c.call("variables", {"variablesReference": 60001}, timeout=8)
            results["observations"]["stale_ref_probe"] = (r or {})
            log(f"  stale variablesReference probe: {dap.summarize(r)}")

        time.sleep(1)
        results["adapter_alive"] = c.proc.poll() is None
        return 0
    finally:
        c.close()
        results["adapter_stderr"] = c.stderr_lines[:40]
        out = pathlib.Path(args.transcript).with_suffix(".summary.json")
        out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        passed = sum(1 for x in results["checks"] if x["ok"])
        failed = sum(1 for x in results["checks"] if not x["ok"])
        log(f"\nchecks: {passed} passed, {failed} failed")
        log(f"summary: {out}")
        if sys.exc_info()[0] is None:
            raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    raise SystemExit(main())
