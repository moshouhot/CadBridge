#!/usr/bin/env python3
"""Read-only AutoCAD COM worker with identity checks BEFORE document access.

Input: one JSON object on stdin.  Output: one JSON object on stdout.
This worker never launches or terminates AutoCAD.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import safe_process as sp  # noqa: E402


def emit(obj: dict) -> int:
    print(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0


def main() -> int:
    started = time.monotonic()
    try:
        req = json.loads(sys.stdin.readline())
    except Exception as exc:  # noqa: BLE001
        return emit({"status": "refused_request", "error": f"{type(exc).__name__}:{exc}"})

    try:
        expected_pid = int(req["expected_pid"])
        expected_creation = str(req["expected_creation"])
        expected_exe = str(req["expected_exe"])
        progid = str(req["progid"])
    except Exception as exc:  # noqa: BLE001
        return emit({"status": "refused_request", "error": f"{type(exc).__name__}:{exc}"})

    # FIRST BARRIER: verify the process-table identity before COM/document access.
    try:
        proc = sp.process_by_pid(expected_pid)
    except sp.EnumerationError as exc:
        return emit({"status": "refused_identity", "error": str(exc)})
    if proc is None:
        return emit({"status": "refused_identity", "error": "expected pid does not exist"})
    if (proc.get("creation") or "").strip() != expected_creation:
        return emit({"status": "refused_identity", "error": "creation time mismatch"})
    if sp.norm_path(proc.get("path") or "") != sp.norm_path(expected_exe):
        return emit({"status": "refused_identity", "error": "executable path mismatch"})

    import pythoncom
    pythoncom.CoInitialize()
    try:
        # GetActiveObject may bind to a different AutoCAD session on a multi-instance system.
        # HWND is application-level metadata and is checked BEFORE ActiveDocument is read.
        app = sp.com_attach_existing(progid)
        ok, reason = sp.com_verify_pid(app, expected_pid)
        if not ok:
            return emit({"status": "refused_hwnd_pid", "error": reason})

        # Only after both identity barriers may drawing state be touched.
        doc = app.ActiveDocument
        ms = doc.ModelSpace
        circles = []
        for i in range(ms.Count):
            ent = ms.Item(i)
            if ent.ObjectName == "AcDbCircle":
                circles.append({
                    "handle": ent.Handle,
                    "center": [ent.Center[0], ent.Center[1], ent.Center[2]],
                    "radius": ent.Radius,
                    "layer": ent.Layer,
                })
        return emit({
            "status": "ok",
            "verified_pid": expected_pid,
            "document_name": doc.Name,
            "circles": circles,
            "total_entities": ms.Count,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        })
    except Exception as exc:  # noqa: BLE001
        return emit({
            "status": "error",
            "error": f"{type(exc).__name__}:{exc}",
            "elapsed_seconds": round(time.monotonic() - started, 3),
        })
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
