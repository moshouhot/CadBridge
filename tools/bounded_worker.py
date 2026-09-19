#!/usr/bin/env python3
"""Bounded subprocess worker used by CadBridge live probes.

The earlier P1 probes put COM calls on daemon threads.  A timed-out daemon thread cannot be
stopped safely; it can continue touching AutoCAD after the harness has already made its
decision.  This module replaces that pattern with a CHILD PROCESS owned by the harness.

Only the worker process is ever terminated here.  This module never terminates AutoCAD.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time


def _finish_after_timeout(proc: subprocess.Popen, grace: float = 3.0) -> tuple[str, str, bool]:
    """Terminate/kill only our child worker and confirm whether it exited."""
    out = err = ""
    try:
        proc.terminate()
    except Exception as exc:  # noqa: BLE001
        err = f"terminate_failed:{type(exc).__name__}:{exc}"
    try:
        out, err2 = proc.communicate(timeout=grace)
        err = (err + "\n" + (err2 or "")).strip()
        return out or "", err, proc.poll() is not None
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
    except Exception as exc:  # noqa: BLE001
        err = (err + f"\nkill_failed:{type(exc).__name__}:{exc}").strip()
    try:
        out2, err2 = proc.communicate(timeout=grace)
        return out2 or out, (err + "\n" + (err2 or "")).strip(), proc.poll() is not None
    except subprocess.TimeoutExpired:
        return out, err, False


def run_worker(worker: str, request: dict, timeout: float, *, grace: float = 3.0) -> dict:
    """Run one JSON worker with a hard process boundary.

    Parent result statuses:
      completed       worker exited 0 and returned one JSON object
      failed          worker exited/non-JSON/returned invalid output
      timed_out       deadline hit and worker exit was confirmed
      outcome_unknown deadline hit and even worker termination could not be confirmed
    """
    started = time.monotonic()
    worker_path = str(pathlib.Path(worker).resolve())
    try:
        proc = subprocess.Popen(
            [sys.executable, worker_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "error": f"worker_launch_failed:{type(exc).__name__}:{exc}",
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }

    payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
    try:
        out, err = proc.communicate(payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        out, err, exited = _finish_after_timeout(proc, grace=grace)
        return {
            "status": "timed_out" if exited else "outcome_unknown",
            "worker_pid": proc.pid,
            "worker_exit_confirmed": bool(exited),
            "returncode": proc.poll(),
            "stdout": (out or "")[:4000],
            "stderr": (err or "")[:4000],
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }

    base = {
        "worker_pid": proc.pid,
        "worker_exit_confirmed": proc.poll() is not None,
        "returncode": proc.returncode,
        "stderr": (err or "")[:4000],
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    if proc.returncode != 0:
        return {"status": "failed", "stdout": (out or "")[:4000], **base}
    try:
        result = json.loads((out or "").strip())
    except json.JSONDecodeError as exc:
        return {
            "status": "failed",
            "error": f"invalid_worker_json:{exc}",
            "stdout": (out or "")[:4000],
            **base,
        }
    if not isinstance(result, dict):
        return {"status": "failed", "error": "worker_result_not_object", **base}
    return {"status": "completed", "result": result, **base}


def run_owned_com_read(ownership_token: str, progid: str, timeout: float) -> dict:
    """Run the read-only COM worker for one process owned by safe_process.

    The private ownership token never leaves this parent.  Only scalar identity facts are
    sent to the child, which re-checks them and then verifies COM HWND -> expected PID before
    touching ActiveDocument.
    """
    import safe_process as sp

    ok, reason = sp.verify_owned(ownership_token, log=lambda *_: None)
    if not ok:
        return {"status": "failed", "error": "ownership_not_verified", "reason": reason}
    rec = sp.owned_process(ownership_token)
    if rec is None:
        return {"status": "failed", "error": "ownership_record_missing"}
    request = {
        "expected_pid": rec.pid,
        "expected_creation": rec.creation,
        "expected_exe": rec.path,
        "progid": progid,
    }
    worker = pathlib.Path(__file__).with_name("com_read_worker.py")
    return run_worker(str(worker), request, timeout)


def _fixture_worker() -> int:
    req = json.loads(sys.stdin.readline())
    mode = req.get("mode")
    if mode == "sleep":
        time.sleep(float(req.get("seconds", 1)))
        print(json.dumps({"status": "late"}))
        return 0
    if mode == "exit":
        return int(req.get("code", 7))
    if mode == "bad-json":
        print("NOT JSON")
        return 0
    print(json.dumps({"status": "ok", "echo": req.get("value")}, separators=(",", ":")))
    return 0


def self_test() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool, detail=None):
        print(f"  [{'OK ' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
        if not cond:
            failures.append(name)

    me = str(pathlib.Path(__file__).resolve())
    # Use this module itself as a tiny fixture worker.
    r = run_worker(me, {"mode": "echo", "value": 42}, 3)
    check("normal worker completes", r.get("status") == "completed", r)
    check("normal worker returns JSON", (r.get("result") or {}).get("echo") == 42, r)

    r = run_worker(me, {"mode": "sleep", "seconds": 2}, 0.1, grace=1)
    check("hung worker is bounded by process timeout", r.get("status") == "timed_out", r)
    check("timed-out worker exit is confirmed", r.get("worker_exit_confirmed") is True, r)

    r = run_worker(me, {"mode": "exit", "code": 9}, 3)
    check("nonzero worker exit fails", r.get("status") == "failed" and r.get("returncode") == 9, r)

    r = run_worker(me, {"mode": "bad-json"}, 3)
    check("invalid worker JSON fails", r.get("status") == "failed", r)

    print(f"\nbounded-worker self-test: {'PASS' if not failures else 'FAIL'} "
          f"({len(failures)} failure(s))")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--fixture-worker", action="store_true")
    args = ap.parse_args()
    if args.fixture_worker:
        return _fixture_worker()
    if args.self_test:
        # run_worker invokes this file without args, so teach the no-arg child form below.
        return self_test()
    # No-argument execution is the fixture-worker protocol used by self_test.
    return _fixture_worker()


if __name__ == "__main__":
    raise SystemExit(main())
