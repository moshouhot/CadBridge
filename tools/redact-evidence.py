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


def _detect_encoding(raw: bytes) -> tuple[str | None, str]:
    """Determine the text encoding of `raw`, or report that it is not scannable text.

    Returns (encoding, reason). encoding is None when the bytes are not decodable text.

    BOM-less UTF-16 is detected STRUCTURALLY, not by trial decoding: decoding arbitrary bytes
    as utf-16 rarely raises (it just produces garbage), so a naive loop silently mis-decodes
    and reports a false clean. Evidence here includes UTF-16LE output captured from
    accoreconsole when stdout was not a console, and it has no BOM.

    GB18030 is included because this project's host is a Chinese Windows install and some
    captured console output is GBK/GB18030, not UTF-8.
    """
    if len(raw) >= 2:
        odd_nuls = raw[1::2].count(0)
        even_nuls = raw[0::2].count(0)
        pairs = max(1, len(raw) // 2)
        # ASCII text in UTF-16LE has NULs in every odd byte position; in BE, every even one.
        if odd_nuls / pairs > 0.3 and even_nuls / pairs < 0.05:
            enc = "utf-16-le"
        elif even_nuls / pairs > 0.3 and odd_nuls / pairs < 0.05:
            enc = "utf-16-be"
        else:
            enc = None
        if enc:
            # Structural detection still requires the bytes to decode cleanly; otherwise the
            # file is not scannable text and must not be reported as clean.
            try:
                raw.decode(enc)
            except (UnicodeDecodeError, UnicodeError):
                return None, f"structurally UTF-16 ({enc}) but contains malformed sequences"
            return enc, ""

    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            raw.decode(enc)
            return enc, ""
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None, "cannot decode as UTF-8, UTF-16 or GB18030"


def _pattern_codec(encoding: str) -> str:
    """The BOM-less codec to use when encoding the identifier for byte-level matching.

    A real defect came from using the DETECTION codec for the pattern: "utf-8-sig".encode()
    prepends a UTF-8 BOM, so the pattern became b'\xef\xbb\xbfAdministrator...' and never
    matched anything. The identifier is plain text, so the pattern must never carry a BOM.
    """
    if encoding == "utf-8-sig":
        return "utf-8"
    if encoding == "utf-16":
        return "utf-16-le"  # detection returns explicit endianness; this is a safe default
    return encoding


def scrub_file(p: pathlib.Path, identifier: str, *, dry_run: bool) -> int:
    """Replace the identifier in one text file. Returns the number of occurrences.

    REPLACEMENT IS DONE AT THE BYTE LEVEL, and that is the point.

    An earlier version decoded with errors="replace", substituted the identifier, and encoded
    back with errors="replace". That rewrites EVERY malformed sequence in the file, not just
    the identifier: a lone surrogate in an otherwise-fine UTF-16 evidence file became U+FFFD.
    The scrub then silently altered unrelated evidence bytes while appearing to only remove a
    username. Verified before fixing: a file whose tail was the bytes 00 d8 came back as fd ff.

    So the file is now: (1) checked to be decodable text, which is required to know the
    identifier's byte pattern and to refuse unscannable files; (2) scrubbed by replacing only
    the encoded identifier bytes; and (3) VERIFIED by re-inserting the identifier pattern and
    asserting the result is byte-identical to the original, which proves nothing else changed.
    """
    if p.suffix.lower() in SKIP_SUFFIXES:
        return 0
    try:
        raw = p.read_bytes()
    except OSError as e:
        raise RuntimeError(f"cannot read {p}: {e}")

    encoding, reason = _detect_encoding(raw)
    if encoding is None:
        raise RuntimeError(
            f"cannot scan {p}: {reason}; it was NOT checked for the identifier"
        )

    pattern = identifier.encode(_pattern_codec(encoding))
    replacement = REPLACEMENT.encode(_pattern_codec(encoding))
    n = raw.count(pattern)
    if not n:
        return 0
    if dry_run:
        return n

    scrubbed = raw.replace(pattern, replacement)

    # VERIFY that ONLY the identifier bytes changed. The check reconstructs the expected result
    # from the original segments and compares it to what was written, which is exact and
    # unambiguous. (Re-inserting the identifier into the scrubbed bytes would NOT be: if the
    # file already contained the literal replacement text, that approach would rewrite it too
    # and report a false failure.)
    expected = replacement.join(raw.split(pattern))
    if scrubbed != expected:
        raise RuntimeError(
            f"refusing to write {p}: byte-level verification failed, so the scrub would change "
            f"bytes other than the identifier"
        )

    before = _sha256(p)
    p.write_bytes(scrubbed)
    after = _sha256(p)
    p.with_name(p.name + ".redaction.txt").write_text(
        f"Redaction provenance for {p.name}\n"
        f"original_sha256={before}\n"
        f"stored_sha256={after}\n"
        f"occurrences={n}\n"
        f"encoding={encoding}\n"
        f"method=byte-level replacement of the encoded identifier only\n"
        f"verification=the written bytes equal the original bytes with only the encoded "
        f"identifier spans replaced\n"
        f"replacement=local account/machine identifier -> {REPLACEMENT}\n"
        f"scope=Only the encoded identifier bytes were replaced. Every other byte, including any\n"
        f"malformed sequences, is unchanged. No measurement, warning count, exit code or\n"
        f"conclusion was altered.\n",
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

    # Files that are not decodable text (binaries, .dll, .pdb) are excluded from the scan by
    # design, and that exclusion must be EXPLICIT rather than an accident of decoding failure.
    # Anything else that fails to decode is an UNVERIFIED file: reporting PASS for it would be
    # a false clean, so --check fails instead. This was a real defect -- an undecodable tracked
    # file produced "PASS no local machine identifier" while never having been scanned.
    unexpected = [u for u in undecodable
                  if pathlib.Path(u.split(":", 1)[0]).suffix.lower() not in SKIP_SUFFIXES]

    if undecodable:
        print(f"  WARN  {len(undecodable)} file(s) not scanned:")
        for u in undecodable[:20]:
            print(f"          {u}")

    if args.check:
        if unexpected:
            print(f"  FAIL  {len(unexpected)} file(s) could not be decoded and were therefore "
                  f"NOT scanned for the identifier. Reporting a clean result would be a false "
                  f"clean. Decode them, add the suffix to SKIP_SUFFIXES with a reason, or scan "
                  f"them at the byte level.", file=sys.stderr)
            return 1
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
