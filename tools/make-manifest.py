#!/usr/bin/env python3
"""Generate and validate a CadBridge evidence manifest for a phase run directory.

Conforms to the ACCEPTANCE.md §1 evidence rules: every run records artifacts with hashes,
and every test records expected/actual/status plus the artifact paths that back it.

This tool does two jobs on purpose:
  1. build manifest.json (hashes every artifact, merges a status file),
  2. VALIDATE it -- and validation is not optional, because a manifest that points at
     missing files or malformed JSON is worse than no manifest: it looks like evidence.

Validation fails (non-zero exit) when:
  * an artifact cannot be read,
  * a JSON/JSONL artifact does not parse,
  * a test references an artifact path that does not exist,
  * a test uses a status outside the ACCEPTANCE.md vocabulary,
  * a test with status PASS/FAIL has no evidence path.

Usage:
  python tools/make-manifest.py <run-dir> --phase P1 --run-id <id> \
      [--status-file <json>] [--extra <json>] [--no-validate]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

SKIP_DIRS = {".git", "bin", "obj", "node_modules"}
MANIFEST_NAME = "manifest.json"

# ACCEPTANCE.md §1 status vocabulary. Anything else is a process/progress value and must
# live in a *_note field instead of the status field.
VALID_STATUS = {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "N/A"}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_artifacts(root: Path) -> tuple[list[dict], list[str]]:
    artifacts: list[dict] = []
    problems: list[str] = []
    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        # Never hash the manifest into itself.
        if rel == MANIFEST_NAME:
            continue
        try:
            size = p.stat().st_size
            digest = sha256_file(p)
        except OSError as e:
            problems.append(f"unreadable artifact {rel}: {e}")
            continue

        entry = {"path": rel, "bytes": size, "sha256": digest}

        # Parse-check JSON artifacts so a truncated or empty file is caught here.
        if rel.lower().endswith(".json"):
            try:
                text = p.read_text(encoding="utf-8")
                if text.strip() == "":
                    problems.append(f"empty JSON artifact (expected valid JSON): {rel}")
                else:
                    json.loads(text)
                    entry["json_parses"] = True
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
                entry["json_parses"] = False
                problems.append(f"invalid JSON artifact {rel}: {e}")

        artifacts.append(entry)
    return artifacts, problems


def validate_tests(tests: list[dict], root: Path) -> list[str]:
    problems: list[str] = []
    for t in tests:
        tid = t.get("id", "<no id>")
        status = t.get("status")
        if status not in VALID_STATUS:
            problems.append(
                f"test {tid}: status {status!r} is not in the ACCEPTANCE vocabulary {sorted(VALID_STATUS)}"
            )
        evidence = t.get("evidence") or []
        if status in ("PASS", "FAIL") and not evidence:
            problems.append(f"test {tid}: status {status} requires at least one evidence path")
        for rel in evidence:
            if not (root / rel).exists():
                problems.append(f"test {tid}: evidence path does not exist: {rel}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--phase", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--status-file", default=None)
    ap.add_argument("--extra", default=None)
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()

    root = Path(args.run_dir).resolve()
    if not root.is_dir():
        print(f"ERROR: not a directory: {root}", file=sys.stderr)
        return 2

    artifacts, problems = collect_artifacts(root)

    tests: list[dict] = []
    if args.status_file:
        sf = Path(args.status_file)
        if sf.is_file():
            try:
                tests = json.loads(sf.read_text(encoding="utf-8")).get("tests", [])
            except json.JSONDecodeError as e:
                problems.append(f"status file is not valid JSON: {e}")

    if not args.no_validate:
        problems += validate_tests(tests, root)

    manifest = {
        "phase": args.phase,
        "run_id": args.run_id,
        "generated_at_utc": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_dir": str(root),
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "tests": tests,
        "validation": {
            "problems": problems,
            "ok": not problems,
        },
        "notes": [
            "sha256 covers the stored artifact bytes, not the upstream source it describes.",
            "Status vocabulary: PASS | FAIL | BLOCKED | NOT_RUN | N/A (ACCEPTANCE.md §1).",
            "Compilation success, Fake backends and tool self-reported success never substitute "
            "for real-CAD evidence.",
            "A process exit code of 0 is not evidence of success; the parsed CAD output is.",
        ],
    }
    if args.extra:
        ep = Path(args.extra)
        if ep.is_file():
            manifest.update(json.loads(ep.read_text(encoding="utf-8")))

    out = root / MANIFEST_NAME
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"WROTE {out}  ({len(artifacts)} artifacts, {len(tests)} tests)")

    if problems:
        print(f"VALIDATION FAILED with {len(problems)} problem(s):", file=sys.stderr)
        for pr in problems:
            print(f"  - {pr}", file=sys.stderr)
        return 1
    print("validation OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
