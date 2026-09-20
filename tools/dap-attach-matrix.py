#!/usr/bin/env python3
"""CadBridge P1/T01-3: definitive attach test against the REAL VS Code extension protocol.

Authoritative source (blob-verified against docs/evidence/P0/.../source-register.json):

  extension/src/debug.ts  blob 33f37992fe82aa4f07ae61f107b232d6f535a7de
    class AttachDebugAdapterExecutableFactory:
        return new vscode.DebugAdapterExecutable(lispadapterpath);      // NO ARGS
    class LispAttachConfigurationProvider:
        newConfig.type    = 'attachlisp';
        newConfig.request = 'attach';
        newConfig.program = <active editor file>;
        newConfig.processId = processId;                                // STRING from pickProcess

  extension/src/process/acadPicker.ts
    export function pickProcess(ports:any, defaultPid: number): Promise<string | null>

So a faithful reproduction must send, to a BARE adapter process:
    initialize { adapterID: ... }
    attach     { type: 'attachlisp', request: 'attach', program: <file>, processId: '<string>' }

Earlier probes violated all three details (adapter was spawned with '-- <acad>', the type was
'autolisp', and processId was an integer), which is why attach looked unresponsive.

This script varies the dimensions that could still matter, and records every attempt:

  A. bare adapter + type attachlisp + string pid + program      (exact extension shape)
  B. bare adapter + type attachlisp + string pid, no program
  C. bare adapter + type autolisp  + string pid + program       (type sensitivity)
  D. bare adapter + type attachlisp + INTEGER pid + program     (pid type sensitivity)
  E. bare adapter + no type       + string pid + program        (type optionality)

Target AutoCAD is launched by THIS script in an isolated, disposable way: a scratch profile
directory is used so the user's real profile is never touched, and only the PID we started is
ever terminated.
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


def find_acad_pids(install_dir: str) -> list[int]:
    return [int(p["pid"]) for p in sp.install_pids(install_dir, ["acad.exe"])]


# NOTE: there is deliberately no `kill_pid(pid)` helper. Terminating by PID alone is unsafe
# because Windows reuses PIDs; cleanup goes through safe_process.terminate_owned, which
# re-verifies pid + creation time + executable path before killing anything.


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--acad", required=True, help="path to acad.exe")
    ap.add_argument("--program", required=True)
    ap.add_argument("--out", required=True, help="JSON results path")
    ap.add_argument("--transcript-dir", required=True)
    ap.add_argument("--timeout", type=float, default=20.0)
    args = ap.parse_args()

    # DISABLED until the safety review passes (see REVIEW-REMEDIATION-round2.md).
    sp.require_safety_review_passed("dap-attach-matrix.py")

    install_dir = str(pathlib.Path(args.acad).parent)
    tdir = pathlib.Path(args.transcript_dir)
    tdir.mkdir(parents=True, exist_ok=True)
    results: dict = {"install_dir": install_dir, "attempts": []}

    pre = find_acad_pids(install_dir)
    if pre:
        log(f"REFUSING: acad.exe from this install is already running: {pre}")
        return 2

    # ---- launch an isolated CAD ------------------------------------------------
    # Use a WINDOWS path: os.environ["TEMP"] is /tmp under MSYS bash. NOTE: AutoCAD was later
    # measured to start fine with a Unix ACAD_USERPROFILE, so this is a preference, not a
    # requirement -- the original "host would fail to start" claim was wrong.
    #
    # NOTE: ACAD_USERPROFILE is set, but its ability to isolate the user's real profile has
    # NOT been demonstrated. It is recorded as an environment variable, not as a guarantee.
    scratch = pathlib.Path("F:/CadBridge-run/_attach-scratch") / f"matrix-{os.getpid()}"
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["ACAD_USERPROFILE"] = str(scratch)
    log(f"launching isolated CAD: {args.acad}")
    owned_token = sp.launch_and_record(args.acad, cwd=install_dir, env=env,
                                 role="t01-matrix", log=log)
    if owned_token is None:
        log("ERROR: CAD did not start")
        return 3
    _rec = sp.owned_process(owned_token)
    pid = _rec.pid if _rec else None
    log(f"CAD pid={pid} (ownership recorded: creation + exe path)")
    results["acad_pid"] = pid

    try:
        # Let AutoCAD reach an idle command line before attaching.
        log("waiting 45s for CAD to become idle ...")
        time.sleep(45)

        cases = [
            ("A_exact_extension_shape", "attachlisp", "attach", str(pid), True),
            ("B_no_program",            "attachlisp", "attach", str(pid), False),
            ("C_type_autolisp",         "autolisp",   "attach", str(pid), True),
            ("D_integer_pid",           "attachlisp", "attach", pid,       True),
            ("E_no_type",               None,         "attach", str(pid), True),
        ]

        for name, typ, req, pid_val, with_program in cases:
            tpath = tdir / f"{name}.jsonl"
            if tpath.exists():
                tpath.unlink()
            log(f"\n=== {name}: type={typ!r} request={req!r} pid={pid_val!r} program={with_program} ===")
            # A fresh bare adapter per case: the adapter is one-shot per session.
            client = dap.DapClient([args.adapter], str(tpath), timeout=args.timeout)
            entry: dict = {"case": name, "type": typ, "request": req,
                           "pid_value": pid_val, "pid_is_string": isinstance(pid_val, str),
                           "with_program": with_program}
            try:
                r = client.call("initialize", {
                    "clientID": "cadbridge-attach", "clientName": "CadBridge Attach",
                    "adapterID": "autolisp", "pathFormat": "path",
                    "linesStartAt1": True, "columnsStartAt1": True,
                })
                entry["initialize_success"] = bool(r and r.get("success"))
                entry["capabilities"] = (r or {}).get("body")
                log(f"  initialize: success={entry['initialize_success']}")

                a = {"processId": pid_val, "request": req}
                if typ is not None:
                    a["type"] = typ
                if with_program:
                    a["program"] = args.program
                r = client.call("attach", a, timeout=args.timeout)
                if r is None:
                    entry["attach"] = "NO_RESPONSE"
                    log("  attach: NO RESPONSE")
                else:
                    entry["attach_success"] = bool(r.get("success"))
                    entry["attach_body"] = r.get("body")
                    entry["attach_error"] = r.get("message")
                    log(f"  attach: success={r.get('success')} body={(r.get('body'))} msg={r.get('message')}")

                    if r.get("success"):
                        # If attach worked, prove the session is live.
                        bp = client.call("setBreakpoints", {
                            "source": {"path": args.program, "name": os.path.basename(args.program)},
                            "breakpoints": [{"line": 6}], "lines": [6],
                        }, timeout=args.timeout)
                        entry["setBreakpoints"] = (bp or {}).get("body")
                        log(f"  setBreakpoints: {(bp or {}).get('body')}")
                        cd = client.call("configurationDone", {}, timeout=args.timeout)
                        entry["configurationDone_success"] = bool(cd and cd.get("success"))
                        th = client.call("threads", {}, timeout=args.timeout)
                        entry["threads"] = (th or {}).get("body")
                        log(f"  threads: {entry['threads']}")

                # Collect any unsolicited events (e.g. acadnosupport, dgbfatalerr, output).
                time.sleep(2)
                entry["events"] = [{"event": e.get("event"), "body": e.get("body")} for e in client.events]
                for e in entry["events"]:
                    log(f"  event: {e['event']} {e['body']}")
                entry["adapter_alive"] = client.proc.poll() is None
            except Exception as exc:  # noqa: BLE001
                entry["exception"] = f"{type(exc).__name__}: {exc}"
                log(f"  EXCEPTION: {entry['exception']}")
            finally:
                client.close()
                entry["adapter_stderr"] = client.stderr_lines[:20]
            results["attempts"].append(entry)
            time.sleep(3)
    finally:
        cleanup_ok = True
        if pid is not None:
            log(f"\nterminating only our CAD pid={pid}")
            # Verified-ownership termination (pid + creation time + exe path).
            cleanup_ok = sp.terminate_owned(owned_token, log=log)
        results["cleanup_confirmed"] = bool(cleanup_ok)
        pathlib.Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
        log(f"results: {args.out}")
        if sys.exc_info()[0] is None and not cleanup_ok:
            raise SystemExit(1)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
