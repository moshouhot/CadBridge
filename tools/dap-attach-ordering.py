#!/usr/bin/env python3
"""CadBridge P1/T01-3: test the DAP ordering hypothesis for `attach`.

Evidence that motivates this test (docs/evidence/P1/.../T01-3/attach-matrix.json):

  * A bare adapter + `attach {type:'attachlisp', request:'attach', program, processId:'<str>'}`
    against a freshly launched AutoCAD 2026 produced NO error event and NO response.
  * Every later attempt in the same AutoCAD instance produced
        dgbfatalerr: "此 AutoCAD 实例当前用于调试其他文件"
    i.e. "this AutoCAD instance is currently being used to debug another file".
  * Therefore attempt A ENGAGED the engine. It simply never answered the `attach` request.

The obvious remaining explanation is DAP ordering. Our client (like a naive client) sent
`attach` and BLOCKED waiting for its response. If this adapter only answers `attach` after it
has received `setBreakpoints` and `configurationDone`, then waiting first deadlocks forever.

This script therefore drives the session WITHOUT waiting for the attach response:

    initialize -> (initialized) -> attach [no wait] -> setBreakpoints -> configurationDone
              -> then collect every response and event for a long window

It also runs a control case that waits for the attach response first, so the difference is
attributable to ordering and nothing else.

Each case gets a FRESH AutoCAD instance, because a used instance reports
"currently being used to debug another file" and would contaminate the next case.
Only PIDs started by this script are ever terminated.
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


def decode_events(events: list[dict]) -> list[dict]:
    """The adapter emits UTF-8 Chinese; JSON already carried it, so just surface it."""
    out = []
    for e in events:
        body = e.get("body") or {}
        msg = body.get("output") or body.get("message")
        out.append({"event": e.get("event"), "message": msg, "body": body})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--acad", required=True)
    ap.add_argument("--program", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--transcript-dir", required=True)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--collect", type=float, default=90.0)
    args = ap.parse_args()

    # DISABLED until the safety review passes (see REVIEW-REMEDIATION-round2.md).
    sp.require_safety_review_passed("dap-attach-ordering.py")

    install_dir = str(pathlib.Path(args.acad).parent)
    tdir = pathlib.Path(args.transcript_dir)
    tdir.mkdir(parents=True, exist_ok=True)
    results: dict = {"cases": []}

    if acad_pids(install_dir):
        log("REFUSING: an acad.exe from this install is already running")
        return 2

    def run_case(name: str, wait_for_attach_response: bool) -> dict:
        log(f"\n########## {name} (wait_for_attach_response={wait_for_attach_response}) ##########")
        entry: dict = {"case": name, "wait_for_attach_response": wait_for_attach_response}

        log("launching fresh isolated CAD ...")
        # Use a WINDOWS path: os.environ["TEMP"] is /tmp under MSYS bash, and while AutoCAD
        # was later measured to start fine with a Unix ACAD_USERPROFILE, a Windows path
        # avoids depending on that behaviour.
        #
        # CORRECTION: an earlier version of this comment claimed a Unix ACAD_USERPROFILE made
        # AutoCAD "silently fail to start". That was WRONG. The real cause of the "CAD did
        # not start" symptom was the process-detection bug fixed in _norm() below: AutoCAD
        # reports its CommandLine with forward slashes, so a backslash install-dir substring
        # never matched. Tested directly on 2026-09-18: ACAD_USERPROFILE=/tmp/... starts fine.
        scratch = pathlib.Path("F:/CadBridge-run/_attach-scratch") / f"{name}-{os.getpid()}"
        scratch.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["ACAD_USERPROFILE"] = str(scratch)
        owned_token = sp.launch_and_record(args.acad, cwd=install_dir, env=env,
                                          role=f"t01-{name}", log=log)
        owned_rec = sp.owned_process(owned_token)
        pid = owned_rec.pid if owned_rec else None
        if pid is None:
            entry["error"] = "CAD did not start (no verifiable launched process)"
            return entry
        entry["acad_pid"] = pid
        log(f"CAD pid={pid}; waiting 45s for idle ...")
        time.sleep(45)

        tpath = tdir / f"{name}.jsonl"
        if tpath.exists():
            tpath.unlink()
        client = dap.DapClient([args.adapter], str(tpath), timeout=args.timeout)
        try:
            r = client.call("initialize", {
                "clientID": "cadbridge-order", "clientName": "CadBridge Order",
                "adapterID": "autolisp", "pathFormat": "path",
                "linesStartAt1": True, "columnsStartAt1": True,
            })
            entry["initialize_success"] = bool(r and r.get("success"))
            ev = client.wait_event("initialized", timeout=10)
            entry["initialized_event"] = ev is not None
            log(f"  initialize={entry['initialize_success']} initialized_event={entry['initialized_event']}")

            attach_args = {
                "type": "attachlisp", "request": "attach",
                "processId": str(pid), "program": args.program,
            }

            if wait_for_attach_response:
                log("  attach (WAITING for response, naive client) ...")
                r = client.call("attach", attach_args, timeout=args.timeout)
                entry["attach"] = "NO_RESPONSE" if r is None else {
                    "success": r.get("success"), "body": r.get("body"), "message": r.get("message")}
                log(f"  attach -> {entry['attach']}")
                if r is None:
                    entry["events_after_attach_wait"] = decode_events(client.events)
                    return entry
            else:
                log("  attach (NOT waiting; sending immediately) ...")
                client.send("attach", attach_args)
                time.sleep(0.5)
                log("  setBreakpoints ...")
                client.send("setBreakpoints", {
                    "source": {"path": args.program, "name": os.path.basename(args.program)},
                    "breakpoints": [{"line": 6}], "lines": [6],
                })
                time.sleep(0.5)
                log("  configurationDone ...")
                client.send("configurationDone", {})

            log(f"  collecting responses/events for {args.collect:.0f}s ...")
            end = time.time() + args.collect
            seen: list = []
            while time.time() < end:
                # responses is a dict keyed by request_seq (see dap-probe.DapClient).
                while client.responses:
                    seq, resp = client.responses.popitem()
                    seen.append({"kind": "response", "command": resp.get("command"),
                                 "success": resp.get("success"), "body": resp.get("body"),
                                 "message": resp.get("message")})
                    log(f"    response {resp.get('command')} success={resp.get('success')} "
                        f"body={json.dumps(resp.get('body'), ensure_ascii=False)[:160]}")
                while client.events:
                    evt = client.events.pop(0)
                    seen.append({"kind": "event", "event": evt.get("event"), "body": evt.get("body")})
                    log(f"    event {evt.get('event')} {json.dumps(evt.get('body'), ensure_ascii=False)[:160]}")
                if any(s.get("kind") == "response" and s.get("command") == "attach" for s in seen):
                    # attach answered; keep collecting a little longer for breakpoints/stopped
                    end = min(end, time.time() + 25)
                time.sleep(0.2)
            entry["messages"] = seen
            entry["adapter_alive"] = client.proc.poll() is None
        except Exception as exc:  # noqa: BLE001
            entry["exception"] = f"{type(exc).__name__}: {exc}"
            log(f"  EXCEPTION {entry['exception']}")
        finally:
            client.close()
            entry["adapter_stderr"] = client.stderr_lines[:20]
            log(f"  terminating our CAD pid={pid}")
            # Verified-ownership termination only (pid + creation time + exe path).
            sp.terminate_owned(owned_token, log=log)   # token only; a dict cannot authorize a kill
        return entry

    try:
        results["cases"].append(run_case("no_wait_ordering", wait_for_attach_response=False))
        time.sleep(5)
        results["cases"].append(run_case("wait_control", wait_for_attach_response=True))
    finally:
        pathlib.Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
        log(f"\nresults: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
