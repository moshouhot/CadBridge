#!/usr/bin/env python3
"""Scrub local machine identifiers out of captured evidence before it is committed.

WHY THIS EXISTS
---------------
Evidence is captured by running real commands on a real Windows host, so the raw output
contains machine-local paths such as

    C:\\Users\\<account>.<machine>\\.nuget\\packages\\...

Committing that to a public repository publishes the local account and machine name. This
happened twice during the baseline audit: once in four P0/P1 artifacts, and again in the P2
build log produced while fixing the first leak. A one-off manual fix is not a fix, so the
scrub is a tool and `selftest.sh` fails if the identifier ever reappears in a tracked file.

DESIGN
------
* Only the account/machine identifier inside paths is replaced, with <REDACTED-USER>.
  Measurements, warning counts, exit codes and conclusions are untouched -- this is a
  privacy scrub, not an evidence edit.
* Every scrub writes a `<file>.redaction.txt` sidecar recording the original SHA-256, the
  stored SHA-256 and the occurrence count, so the change is auditable rather than invisible.
* The identifier itself is supplied via --identifier or the CB_REDACT_IDENTIFIER environment
  variable and is never hard-coded here, because this file is public: hard-coding it would
  re-publish the very string the tool removes.

Usage:
  redact-evidence.py [PATH ...] [--identifier NAME] [--check] [--dry-run]

  --check     Report occurrences and exit 1 if any remain (used by selftest.sh). Writes
              nothing. Scans tracked files only when no PATH is given.
  --dry-run   Report what would change without writing.

Exit codes: 0 = clean (or successfully scrubbed), 1 = --check found occurrences,
2 = usage/configuration error.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import subprocess
import sys

DEFAULT_IDENTIFIER = os.environ.get("CB_REDACT_IDENTIFIER", "")
REPLACEMENT = "<REDACTED-USER>"

# Binary or non-evidence content is skipped: scrubbing it would corrupt it, and the
# identifier cannot appear in it in a meaningful way.
SKIP_SUFFIXES = {".dll", ".exe", ".pdb", ".png", ".jpg", ".zip", ".dwg", ".pyc"}


def _sha256(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _tracked_files(root: pathlib.Path) -> list[pathlib.Path]:
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return [root / line for line in out.stdout.splitlines() if line.strip()]


def scrub_file(p: pathlib.Path, identifier: str, *, dry_run: bool) -> int:
    """Replace the identifier in one text file. Returns the number of occurrences."""
    if p.suffix.lower() in SKIP_SUFFIXES:
        return 0
    try:
        raw = p.read_bytes()
    except OSError:
        return 0
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # UTF-16 evidence exists in this repo (accoreconsole output). Handle it explicitly
        # rather than skipping, because that is exactly where paths show up.
        try:
            text = raw.decode("utf-16")
            encoding = "utf-16"
        except UnicodeDecodeError:
            return 0
    else:
        encoding = "utf-8"

    n = text.count(identifier)
    if not n:
        return 0
    if dry_run:
        return n

    before = _sha256(p)
    p.write_bytes(text.replace(identifier, REPLACEMENT).encode(encoding))
    after = _sha256(p)
    p.with_name(p.name + ".redaction.txt").write_text(
        f"Redaction provenance for {p.name}\n"
        f"original_sha256={before}\n"
        f"stored_sha256={after}\n"
        f"occurrences={n}\n"
        f"replacement=local account/machine identifier -> {REPLACEMENT}\n"
        f"scope=Only the identifier inside machine-local paths was replaced. No measurement,\n"
        f"warning count, exit code or conclusion was altered.\n",
        encoding="utf-8",
    )
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--identifier", default=DEFAULT_IDENTIFIER,
                    help="the local identifier to remove (default: $CB_REDACT_IDENTIFIER)")
    ap.add_argument("--check", action="store_true",
                    help="report occurrences and exit 1 if any remain; write nothing")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.identifier:
        print("ERROR: no identifier given. Pass --identifier or set CB_REDACT_IDENTIFIER.\n"
              "It is intentionally not hard-coded here, because this file is public.",
              file=sys.stderr)
        return 2

    root = pathlib.Path(".").resolve()
    if args.paths:
        targets: list[pathlib.Path] = []
        for raw in args.paths:
            p = pathlib.Path(raw)
            if p.is_dir():
                targets.extend(sorted(x for x in p.rglob("*") if x.is_file()))
            elif p.is_file():
                targets.append(p)
    else:
        targets = _tracked_files(root)

    total = 0
    hit_files: list[str] = []
    for p in targets:
        n = scrub_file(p, args.identifier, dry_run=args.check or args.dry_run)
        if n:
            total += n
            hit_files.append(str(p))
            if not args.check:
                verb = "would redact" if args.dry_run else "redacted"
                print(f"  {verb} {n:3d} occurrence(s)  {p}")

    if args.check:
        if total:
            print(f"  FAIL  local identifier found in {len(hit_files)} tracked file(s), "
                  f"{total} occurrence(s):")
            for f in hit_files[:20]:
                print(f"          {f}")
            print("        Run tools/redact-evidence.py to scrub it, and record the "
                  "provenance sidecar.", file=sys.stderr)
            return 1
        print("  PASS  no local machine identifier in tracked files")
        return 0

    print(f"total occurrences {'found' if args.dry_run else 'redacted'}: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
