#!/usr/bin/env python3
"""Minimal live AutoCAD ownership smoke using a retained Windows Job Object.

This is deliberately narrower than every P1 functional probe. It proves only:
  * no target-install AutoCAD existed before launch;
  * the host was created suspended and assigned to our private Job Object before resume;
  * the selected acad.exe is a member of that Job Object and has stable identity metadata;
  * cleanup targets the retained Job Object only;
  * no target-install acad.exe remains afterwards.

It does NOT load CadBridge, attach DAP, call COM, open a production DWG, or modify CAD
security/profile settings.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import safe_process as sp  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--acad", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--settle", type=float, default=15.0)
    args = ap.parse_args()

    sp.require_safety_review_passed("live-job-ownership-smoke.py")

    acad = str(pathlib.Path(args.acad).resolve())
    install_dir = str(pathlib.Path(acad).parent)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    R: dict = {"checks": [], "acad": acad, "started_at": time.time()}

    def check(name: str, ok: bool, detail=None):
        R["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"  [{'OK ' if ok else 'FAIL'}] {name}" +
              (f" :: {detail}" if detail is not None else ""), flush=True)

    pre = sp.list_processes(["acad.exe"])
    R["preexisting"] = pre
    check("no acad.exe existed anywhere before launch", not pre, pre)
    if pre:
        out.write_text(json.dumps(R, indent=2, ensure_ascii=False), encoding="utf-8")
        return 2

    scratch = pathlib.Path("F:/CadBridge-run/_ownership-smoke") / f"smoke-{os.getpid()}"
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["ACAD_USERPROFILE"] = str(scratch)

    token = sp.launch_job_and_record(
        acad, args=["/nologo"], cwd=install_dir, env=env, role="ownership-smoke",
        wait_timeout=60.0, host_exe_name="acad.exe", log=lambda m: print(m, flush=True))
    if token is None:
        check("Job-owned acad.exe launched", False, "launch_job_and_record returned None")
        out.write_text(json.dumps(R, indent=2, ensure_ascii=False), encoding="utf-8")
        return 3

    rec = sp.owned_process(token)
    R["owned"] = rec.audit() if rec else None
    cleanup_ok = False
    try:
        check("Job-owned acad.exe launched", rec is not None, R["owned"])
        if rec is None:
            return 4
        time.sleep(max(0.0, args.settle))
        ok, reason = sp.verify_owned(token)
        R["verify"] = {"ok": ok, "reason": reason}
        check("primary acad.exe identity + Job membership re-verify", ok, reason)
        members = rec.handle.job_process_ids()
        R["job_members_before_cleanup"] = members
        check("primary acad.exe PID is inside retained Job Object", rec.pid in members,
              {"primary": rec.pid, "members": members})
        all_acad = sp.list_processes(["acad.exe"])
        foreign = [p for p in all_acad if int(p.get("pid") or 0) not in set(members)]
        R["foreign_acad_during_smoke"] = foreign
        check("exclusive CAD environment remained intact during smoke", not foreign, foreign)
    finally:
        cleanup_ok = sp.terminate_owned(token, log=lambda m: print(m, flush=True))
        R["cleanup_ok"] = cleanup_ok

    time.sleep(1.0)
    post = sp.list_processes(["acad.exe"])
    R["postexisting"] = post
    check("Job Object cleanup confirmed", cleanup_ok, cleanup_ok)
    check("no acad.exe remains after cleanup", not post, post)
    failed = [x["name"] for x in R["checks"] if not x["ok"]]
    R["verdict"] = "PASS" if not failed else "FAIL"
    R["failed"] = failed
    out.write_text(json.dumps(R, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"VERDICT: {R['verdict']} -> {out}", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
