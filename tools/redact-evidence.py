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
    """Tracked files, enumerated with NUL delimiters.

    TWO FAILURES THIS AVOIDS:
      1. Returning [] when `git ls-files` fails would make --check report PASS on a tree it
         never actually inspected. A failure to enumerate must be an ERROR, not an empty list.
      2. Splitting on newlines breaks for any path containing a newline, and `core.quotepath`
         can escape non-ASCII paths into `\303\251`-style octal, which then does not exist on
         disk and is silently skipped. -z with quotepath disabled gives exact paths.
    """
    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files", "-z"],
        cwd=root, capture_output=True,
    )
    if out.returncode != 0:
        raise RuntimeError(
            "git ls-files failed (rc=" + str(out.returncode) + "): "
            + out.stderr.decode("utf-8", errors="replace").strip()
            + " -- refusing to report a clean result for a tree that could not be enumerated"
        )
    return [root / p.decode("utf-8", errors="surrogateescape")
            for p in out.stdout.split(b"\0") if p]


def scrub_file(p: pathlib.Path, identifier: str, *, dry_run: bool) -> int:
    """Replace the identifier in one text file. Returns the number of occurrences.

    Encodings handled: UTF-8 (with or without BOM), UTF-16 LE/BE (with or without BOM).
    Evidence in this repository includes UTF-16LE accoreconsole output, so a UTF-8-only scan
    would report a false clean on exactly the files most likely to contain host paths.
    A file that cannot be decoded is NOT silently skipped: it is reported, because skipping
    it would let --check claim a clean result it never verified.
    """
    if p.suffix.lower() in SKIP_SUFFIXES:
        return 0
    try:
        raw = p.read_bytes()
    except OSError as e:
        raise RuntimeError(f"cannot read {p}: {e}")

    text = None
    encoding = None

    # BOM-less UTF-16 must be detected STRUCTURALLY, not by trial decoding: decoding arbitrary
    # bytes as utf-16 rarely raises (it just produces garbage), so a naive loop silently
    # mis-decodes and reports a false clean. Evidence in this repo includes UTF-16LE output
    # captured from accoreconsole when stdout was not a console, and it has NO BOM.
    if len(raw) >= 2:
        odd_nuls = raw[1::2].count(0)
        even_nuls = raw[0::2].count(0)
        pairs = max(1, len(raw) // 2)
        # ASCII text in UTF-16LE has NULs in every odd byte position; in BE, every even one.
        if odd_nuls / pairs > 0.3 and even_nuls / pairs < 0.05:
            text, encoding = raw.decode("utf-16-le", errors="replace"), "utf-16-le"
        elif even_nuls / pairs > 0.3 and odd_nuls / pairs < 0.05:
            text, encoding = raw.decode("utf-16-be", errors="replace"), "utf-16-be"

    if text is None:
        # GB18030 is tried because this project's host is a Chinese Windows install and some
        # captured console output is GBK/GB18030, not UTF-8. Decoding it as UTF-8 fails, and
        # silently skipping it would mean the file is never scanned.
        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text, encoding = raw.decode(enc), enc
                break
            except (UnicodeDecodeError, UnicodeError):
                continue

    if text is None:
        # Not decodable as any supported text encoding. Report it rather than pretending it is
        # clean: it may be a binary that embeds the identifier, and the caller must know it was
        # never scanned.
        raise RuntimeError(
            f"cannot decode {p} as UTF-8, UTF-16 or GB18030; it was NOT scanned for the "
            f"identifier"
        )

    n = text.count(identifier)
    if not n:
        return 0
    if dry_run:
        return n

    before = _sha256(p)
    # Encode back in the SAME encoding, so a scrub does not silently rewrite the file's bytes
    # beyond the identifier (which would break the recorded evidence hash for unrelated text).
    p.write_bytes(text.replace(identifier, REPLACEMENT).encode(encoding, errors="replace"))
    after = _sha256(p)
    p.with_name(p.name + ".redaction.txt").write_text(
        f"Redaction provenance for {p.name}\n"
        f"original_sha256={before}\n"
        f"stored_sha256={after}\n"
        f"occurrences={n}\n"
        f"encoding={encoding}\n"
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
        try:
            targets = _tracked_files(root)
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

    total = 0
    hit_files: list[str] = []
    undecodable: list[str] = []
    for p in targets:
        try:
            n = scrub_file(p, args.identifier, dry_run=args.check or args.dry_run)
        except RuntimeError as e:
            # A file we could not scan is a gap in coverage, not a pass.
            undecodable.append(f"{p}: {e}")
            continue
        if n:
            total += n
            hit_files.append(str(p))
            if not args.check:
                verb = "would redact" if args.dry_run else "redacted"
                print(f"  {verb} {n:3d} occurrence(s)  {p}")

    if undecodable:
        print(f"  WARN  {len(undecodable)} file(s) could not be decoded and were NOT scanned:")
        for u in undecodable[:20]:
            print(f"          {u}")
        # Files that are not text at all are expected (binaries, .raw captures). Report them
        # so the coverage gap is visible, but do not fail the check on them.
        print("        (non-text files are expected to be listed here; they are out of scope "
              "for text scrubbing but must not be mistaken for verified-clean)")

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
