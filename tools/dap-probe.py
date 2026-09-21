#!/usr/bin/env python3
"""Minimal Debug Adapter Protocol client used to probe AutoCAD's AutoLISP debug adapter.

WHY THIS EXISTS (P1/T01-3): CadBridge must drive the AutoLISP debugger WITHOUT VS Code.
The P0 static analysis showed the DAP request vocabulary lives in AutoCAD's in-process
`vl_u.crx`, while `AutoLispDebugAdapter.exe` only carries the handshake. That is a
hypothesis about bytes, not about behaviour. This client is how the hypothesis gets tested.

This is a PROBE, not product code. The production client belongs in CadBridge.Debug.AutoCAD
(C#). It is written in Python because it must be quick to change while the real protocol
shape is still unknown.

It speaks the genuine DAP wire format:
    Content-Length: <n>\\r\\n\\r\\n<utf-8 json>
and records every byte in and out so the transcript can be used as evidence.

Usage:
  dap-probe.py --adapter PATH [--adapter-arg ...] [--attach-pid N] [--program FILE]
               [--breakpoint FILE:LINE ...] [--step-in] [--step-over] [--continue]
               [--transcript OUT.jsonl] [--timeout SECS]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import safe_process as sp  # noqa: E402

CONTENT_LENGTH = b"Content-Length: "


class DapClient:
    """Blocking DAP client with a full transcript."""

    def __init__(self, argv: list[str], transcript_path: str | None, timeout: float = 30.0):
        self.argv = argv
        self.timeout = timeout
        self.seq = 0
        self.transcript: list[dict] = []
        self.transcript_path = transcript_path
        self.responses: dict[int, dict] = {}
        self.events: list[dict] = []
        self.recv_index: int = 0
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self.stderr_lines: list[str] = []

        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._err = threading.Thread(target=self._err_loop, daemon=True)
        self._err.start()

    # ---- plumbing -------------------------------------------------------------------
    def _record(self, direction: str, payload: Any) -> None:
        with self.lock:
            entry = {"t": round(time.time(), 4), "dir": direction, "payload": payload}
            self.transcript.append(entry)
            if self.transcript_path:
                with open(self.transcript_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _read_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.proc.stdout.read(n - len(buf))
            if not chunk:
                raise EOFError("adapter stdout closed")
            buf += chunk
        return buf

    def _read_loop(self) -> None:
        try:
            while not self._stop.is_set():
                header = b""
                # Read until the blank line terminating the header block.
                #
                # PROTOCOL QUIRK (observed on AutoLispDebugAdapter.exe, 2026-09-17): the
                # adapter emits a stray LF *before* the next `Content-Length` header, i.e.
                # the stream looks like `...body\nContent-Length: 129\r\n\r\n{...}`.
                # A strict reader that requires the block to begin with 'Content-Length'
                # fails, and -- worse -- an earlier version of this probe RETURNED on that
                # error, going permanently deaf while the adapter was still answering.
                # Everything after `initialize` then looked like "adapter not responding".
                #
                # So: tolerate leading CR/LF bytes, and never abandon the loop on a header
                # anomaly; resynchronise instead.
                while not header.endswith(b"\r\n\r\n"):
                    ch = self.proc.stdout.read(1)
                    if not ch:
                        return
                    if header == b"" and ch in (b"\n", b"\r"):
                        continue  # skip stray inter-message newlines
                    header += ch
                    if len(header) > 8192:
                        self._record("error", {"reason": "header too long",
                                               "header": header[:200].decode("latin1")})
                        header = b""
                        continue

                length = None
                for line in header.split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        try:
                            length = int(line.split(b":", 1)[1].strip())
                        except ValueError:
                            length = None
                if length is None:
                    # Resynchronise rather than die: a malformed header must not silence the
                    # rest of the session.
                    self._record("error", {"reason": "no Content-Length",
                                           "header": header.decode("latin1")})
                    continue

                body = self._read_exact(length)
                try:
                    msg = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    self._record("error", {"reason": f"bad json: {e}", "body": body[:300].decode("utf-8", "replace")})
                    continue

                self._record("in", msg)
                mtype = msg.get("type")
                if mtype == "response":
                    with self.lock:
                        self.responses[msg.get("request_seq", -1)] = msg
                elif mtype == "event":
                    with self.lock:
                        # `recv_index` is a monotonically increasing LOCAL arrival counter.
                        # The protocol's own `seq` field is assigned by the adapter and is
                        # NOT a reliable local cursor (it can repeat across sessions and says
                        # nothing about how many chunks we have already consumed). Consumers
                        # that must read each event exactly once should use recv_index.
                        self.recv_index += 1
                        msg["recv_index"] = self.recv_index
                        self.events.append(msg)
        except (EOFError, OSError):
            return
        except Exception as e:  # noqa: BLE001 - probe must never die silently
            self._record("error", {"reason": f"{type(e).__name__}: {e}"})

    def _err_loop(self) -> None:
        try:
            for raw in iter(self.proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    self.stderr_lines.append(line)
                    self._record("stderr", {"line": line})
        except (OSError, ValueError):
            return

    def send(self, command: str, arguments: dict | None = None) -> int:
        self.seq += 1
        msg = {"seq": self.seq, "type": "request", "command": command}
        if arguments is not None:
            msg["arguments"] = arguments
        body = json.dumps(msg).encode("utf-8")
        self._record("out", msg)
        self.proc.stdin.write(CONTENT_LENGTH + str(len(body)).encode() + b"\r\n\r\n" + body)
        self.proc.stdin.flush()
        return self.seq

    def wait_response(self, seq: int, timeout: float | None = None) -> dict | None:
        deadline = time.time() + (timeout if timeout is not None else self.timeout)
        while time.time() < deadline:
            with self.lock:
                if seq in self.responses:
                    return self.responses.pop(seq)
            if self.proc.poll() is not None:
                return None
            time.sleep(0.05)
        return None

    def wait_event(self, name: str, timeout: float | None = None) -> dict | None:
        deadline = time.time() + (timeout if timeout is not None else self.timeout)
        while time.time() < deadline:
            with self.lock:
                for e in self.events:
                    if e.get("event") == name:
                        return e
            if self.proc.poll() is not None:
                return None
            time.sleep(0.05)
        return None

    def call(self, command: str, arguments: dict | None = None, timeout: float | None = None) -> dict | None:
        seq = self.send(command, arguments)
        return self.wait_response(seq, timeout)

    def close(self) -> None:
        self._stop.set()
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except OSError:
            pass


def summarize(resp: dict | None) -> str:
    if resp is None:
        return "NO RESPONSE (timeout or adapter exited)"
    if resp.get("success"):
        body = resp.get("body")
        return "success" + (f" body_keys={sorted(body)}" if isinstance(body, dict) else "")
    return f"FAILURE message={resp.get('message')!r} body={resp.get('body')!r}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--adapter-arg", action="append", default=[])
    ap.add_argument("--attach-pid", type=int)
    ap.add_argument("--program")
    ap.add_argument("--breakpoint", action="append", default=[], help="FILE:LINE")
    ap.add_argument("--step-in", action="store_true")
    ap.add_argument("--step-over", action="store_true")
    ap.add_argument("--continue", dest="do_continue", action="store_true")
    ap.add_argument("--stack", action="store_true")
    ap.add_argument("--scopes", action="store_true")
    ap.add_argument("--variables", action="store_true")
    ap.add_argument("--transcript")
    ap.add_argument("--timeout", type=float, default=20.0)
    args = ap.parse_args()

    # This utility is also useful for adapter-only protocol probing, which does not touch a
    # CAD session.  The moment it can attach/launch against CAD, however, it is a live
    # harness and must obey the same hard safety gate as the dedicated T01 scripts.
    live_intent = (
        args.attach_pid is not None
        or bool(args.program)
        or any("acad.exe" in sp.norm_path(str(x)) for x in args.adapter_arg)
    )
    if live_intent:
        sp.require_safety_review_passed("dap-probe.py")

    if not os.path.isfile(args.adapter):
        print(f"ERROR: adapter not found: {args.adapter}", file=sys.stderr)
        return 2

    if args.transcript and os.path.exists(args.transcript):
        os.remove(args.transcript)

    argv = [args.adapter] + args.adapter_arg
    print(f"spawning: {argv}")
    client = DapClient(argv, args.transcript, timeout=args.timeout)

    try:
        print("\n--- initialize ---")
        resp = client.call("initialize", {
            "clientID": "cadbridge-dap-probe",
            "clientName": "CadBridge DAP Probe",
            "adapterID": "autolisp",
            "pathFormat": "path",
            "linesStartAt1": True,
            "columnsStartAt1": True,
            "supportsVariableType": True,
        })
        print("  " + summarize(resp))
        caps = (resp or {}).get("body") or {}
        if caps:
            print(f"  capabilities: {json.dumps(caps, ensure_ascii=False)[:900]}")

        print("\n--- initialized event ---")
        ev = client.wait_event("initialized", timeout=5)
        print(f"  {ev.get('event') if ev else 'NOT RECEIVED'}")

        if args.attach_pid:
            print(f"\n--- attach (processId={args.attach_pid}) ---")
            attach_args: dict[str, Any] = {"processId": args.attach_pid}
            if args.program:
                attach_args["program"] = args.program
            resp = client.call("attach", attach_args)
            print("  " + summarize(resp))
        elif args.program:
            print(f"\n--- launch (program={args.program}) ---")
            resp = client.call("launch", {"program": args.program, "type": "autolisp", "request": "launch"})
            print("  " + summarize(resp))

        for bp in args.breakpoint:
            path, _, line = bp.rpartition(":")
            print(f"\n--- setBreakpoints {path}:{line} ---")
            resp = client.call("setBreakpoints", {
                "source": {"path": path, "name": os.path.basename(path)},
                "breakpoints": [{"line": int(line)}],
                "lines": [int(line)],
            })
            print("  " + summarize(resp))
            body = (resp or {}).get("body") or {}
            if body.get("breakpoints"):
                print(f"  breakpoints: {json.dumps(body['breakpoints'], ensure_ascii=False)}")

        print("\n--- configurationDone ---")
        print("  " + summarize(client.call("configurationDone", {})))

        print("\n--- threads ---")
        print("  " + summarize(client.call("threads", {})))

        print("\n--- waiting for 'stopped' event (up to 15s) ---")
        ev = client.wait_event("stopped", timeout=15)
        if ev:
            print(f"  STOPPED: {json.dumps(ev.get('body'), ensure_ascii=False)}")
            tid = (ev.get("body") or {}).get("threadId", 1)
            if args.stack:
                print("\n--- stackTrace ---")
                r = client.call("stackTrace", {"threadId": tid})
                print("  " + summarize(r))
                frames = ((r or {}).get("body") or {}).get("stackFrames") or []
                for fr in frames[:8]:
                    print(f"    frame id={fr.get('id')} name={fr.get('name')!r} "
                          f"line={fr.get('line')} source={(fr.get('source') or {}).get('path')}")
                if args.scopes and frames:
                    print("\n--- scopes ---")
                    r = client.call("scopes", {"frameId": frames[0]["id"]})
                    print("  " + summarize(r))
                    scopes = ((r or {}).get("body") or {}).get("scopes") or []
                    for sc in scopes:
                        print(f"    scope {sc.get('name')!r} ref={sc.get('variablesReference')}")
                        if args.variables and sc.get("variablesReference"):
                            vr = client.call("variables", {"variablesReference": sc["variablesReference"]})
                            print("      " + summarize(vr))
                            for v in (((vr or {}).get("body") or {}).get("variables") or [])[:10]:
                                print(f"        {v.get('name')} = {v.get('value')!r}")
        else:
            print("  NO 'stopped' EVENT within 15s")

        for cmd, flag in (("stepIn", args.step_in), ("next", args.step_over), ("continue", args.do_continue)):
            if flag:
                print(f"\n--- {cmd} ---")
                print("  " + summarize(client.call(cmd, {"threadId": 1})))

        time.sleep(1)
        print(f"\n--- adapter still alive: {client.proc.poll() is None} ---")
        return 0
    finally:
        client.close()
        if client.stderr_lines:
            print("\n--- adapter stderr (first 20 lines) ---")
            for line in client.stderr_lines[:20]:
                print("  " + line)


if __name__ == "__main__":
    raise SystemExit(main())
