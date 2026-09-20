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
import stat
import subprocess
import tempfile
import sys

DEFAULT_IDENTIFIER = os.environ.get("CB_REDACT_IDENTIFIER", "")
REPLACEMENT = "<REDACTED-USER>"

# These formats are not safely editable as text. Publication checks FAIL CLOSED when any
# intended target uses one of them: "not inspected" must never be reported as "clean".
UNSCANNABLE_SUFFIXES = {".dll", ".exe", ".pdb", ".png", ".jpg", ".zip", ".dwg", ".pyc"}


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
    """Determine the text encoding when no identifier-specific match is available."""
    if raw.startswith(b"\xff\xfe"):
        try:
            raw.decode("utf-16-le")
            return "utf-16-le", ""
        except UnicodeError:
            return None, "UTF-16LE BOM present but content is malformed"
    if raw.startswith(b"\xfe\xff"):
        try:
            raw.decode("utf-16-be")
            return "utf-16-be", ""
        except UnicodeError:
            return None, "UTF-16BE BOM present but content is malformed"

    if len(raw) >= 2:
        odd_nuls = raw[1::2].count(0)
        even_nuls = raw[0::2].count(0)
        pairs = max(1, len(raw) // 2)
        if odd_nuls / pairs > 0.3 and even_nuls / pairs < 0.05:
            enc = "utf-16-le"
        elif even_nuls / pairs > 0.3 and odd_nuls / pairs < 0.05:
            enc = "utf-16-be"
        else:
            enc = None
        if enc:
            try:
                raw.decode(enc)
            except UnicodeError:
                return None, f"structurally UTF-16 ({enc}) but contains malformed sequences"
            return enc, ""

    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            raw.decode(enc)
            return enc, ""
        except UnicodeError:
            continue
    return None, "cannot decode as UTF-8, UTF-16 or GB18030"


def _encoding_for_identifier(raw: bytes, identifier: str) -> tuple[str | None, str]:
    """Prefer an encoding that actually decodes the requested identifier from the bytes.

    This closes the Chinese-heavy BOM-less UTF-16 gap: those files may contain very few NULs
    overall and can also decode as GB18030. The ASCII identifier itself still has an exact
    UTF-16 byte representation, so verify candidate decodings directly before falling back to
    generic text detection.
    """
    # UTF-16 gets fail-closed treatment. If its exact identifier byte pattern is
    # present but the full file is malformed (for example an odd trailing byte), falling
    # through to GB18030 can produce a false clean because GB18030 accepts the bytes while its
    # single-byte identifier pattern is absent.
    for enc in ("utf-16-le", "utf-16-be"):
        pattern = identifier.encode(enc)
        if not pattern or pattern not in raw:
            continue
        try:
            text = raw.decode(enc)
        except UnicodeError as e:
            return None, (
                f"{enc} identifier byte pattern is present but the file is malformed: {e}"
            )
        if identifier in text:
            return enc, ""

    for enc in ("utf-8", "gb18030"):
        pattern = identifier.encode(enc)
        if not pattern or pattern not in raw:
            continue
        try:
            text = raw.decode(enc)
        except UnicodeError:
            continue
        if identifier in text:
            return enc, ""

    # Windows paths/account names are case-insensitive. A differently-cased identifier must
    # never become a false clean. We intentionally REFUSE rather than case-fold/re-encode the
    # evidence, because preserving every non-identifier byte is a publication invariant.
    folded = identifier.casefold()
    for enc in ("utf-16-le", "utf-16-be", "utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
        except UnicodeError:
            continue
        if folded in text.casefold():
            return None, (
                f"case-insensitive identifier variant is present under {enc}; refusing an "
                "unsafe partial scrub"
            )
    return _detect_encoding(raw)


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


def _path_scope_parts(
    p: pathlib.Path, path_root: pathlib.Path | None
) -> tuple[str, ...]:
    if path_root is None:
        return (p.name,)
    try:
        rel = p.relative_to(path_root)
    except ValueError:
        return (p.name,)
    return tuple(str(part) for part in rel.parts)


def _path_contains_identifier(
    p: pathlib.Path, identifier: str, path_root: pathlib.Path | None
) -> bool:
    needle = identifier.casefold()
    return any(needle in part.casefold() for part in _path_scope_parts(p, path_root))


def _replacement_positions(
    raw: bytes,
    pattern: bytes,
    *,
    encoding: str,
    decoded_exact_count: int,
) -> list[int]:
    """Return non-overlapping byte positions that match decoded identifier spans.

    UTF-16 byte patterns can appear accidentally at odd offsets across unrelated code units.
    Those matches are not decoded identifiers and must never be replaced.
    """
    positions: list[int] = []
    start = 0
    while True:
        pos = raw.find(pattern, start)
        if pos < 0:
            break
        if encoding in ("utf-16-le", "utf-16-be") and pos % 2 != 0:
            start = pos + 1
            continue
        positions.append(pos)
        start = pos + len(pattern)

    if len(positions) != decoded_exact_count:
        raise RuntimeError(
            "raw byte matches do not correspond one-for-one with decoded identifier spans "
            f"(decoded={decoded_exact_count}, replaceable={len(positions)}, encoding={encoding})"
        )
    return positions


def _replace_at_positions(
    raw: bytes, pattern: bytes, replacement: bytes, positions: list[int]
) -> bytes:
    out: list[bytes] = []
    cursor = 0
    for pos in positions:
        if pos < cursor:
            raise RuntimeError("overlapping replacement spans are not allowed")
        out.append(raw[cursor:pos])
        out.append(replacement)
        cursor = pos + len(pattern)
    out.append(raw[cursor:])
    return b"".join(out)


def _remaining_identifier_representations(raw: bytes, identifier: str) -> list[str]:
    """Return supported decodings in which the identifier still appears.

    This is a publication guard, not an encoding detector. Mixed-encoding evidence is allowed
    to be refused: successful scrub means no supported representation may still expose the
    identifier.
    """
    found: list[str] = []
    folded = identifier.casefold()
    for enc in ("utf-16-le", "utf-16-be", "utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
        except UnicodeError:
            continue
        if folded in text.casefold():
            found.append(enc)
    return found


def _stage_bytes(dest: pathlib.Path, data: bytes, mode: int | None) -> pathlib.Path:
    """Write and fsync a same-directory temporary file without publishing it."""
    fd, tmp_name = tempfile.mkstemp(
        dir=str(dest.parent), prefix=dest.name + ".", suffix=".tmp"
    )
    tmp = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        return tmp
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def scrub_file(
    p: pathlib.Path,
    identifier: str,
    *,
    dry_run: bool,
    path_root: pathlib.Path | None = None,
) -> int:
    """Replace the identifier while keeping evidence and provenance failure-safe."""
    if p.is_symlink():
        raise RuntimeError(
            f"refusing to process symlink {p}: replacing it would change repository structure "
            "while leaving the referent untouched"
        )
    if _path_contains_identifier(p, identifier, path_root):
        raise RuntimeError(
            f"refusing to process {p}: a published path component contains the private "
            "identifier; content-only scrubbing cannot make that repository path safe"
        )
    if p.suffix.lower() in UNSCANNABLE_SUFFIXES:
        raise RuntimeError(
            f"cannot verify {p}: suffix {p.suffix.lower()} is not safely scannable/redactable; "
            "refusing to report it clean"
        )
    try:
        raw = p.read_bytes()
    except OSError as e:
        raise RuntimeError(f"cannot read {p}: {e}") from e

    encoding, reason = _encoding_for_identifier(raw, identifier)
    if encoding is None:
        raise RuntimeError(
            f"cannot scan {p}: {reason}; it was NOT checked for the identifier"
        )

    try:
        decoded = raw.decode(encoding)
    except UnicodeError as e:
        raise RuntimeError(f"cannot scan {p}: malformed {encoding}: {e}") from e
    folded_count = decoded.casefold().count(identifier.casefold())
    exact_count = decoded.count(identifier)
    if folded_count != exact_count:
        raise RuntimeError(
            f"cannot safely scrub {p}: case-insensitive identifier variants are present; "
            "refusing a partial redaction"
        )

    pattern = identifier.encode(_pattern_codec(encoding))
    replacement = REPLACEMENT.encode(_pattern_codec(encoding))
    positions = _replacement_positions(
        raw,
        pattern,
        encoding=encoding,
        decoded_exact_count=exact_count,
    )
    n = len(positions)
    if not n:
        return 0

    # Dry-run and real scrub must answer the SAME question: could this exact transformed
    # payload be published safely? Validate the staged bytes before returning from dry-run so
    # a mixed-encoding file cannot be reported as "would redact" when the real scrub refuses.
    scrubbed = _replace_at_positions(raw, pattern, replacement, positions)
    remaining = _remaining_identifier_representations(scrubbed, identifier)
    if remaining:
        raise RuntimeError(
            "refusing to publish a partial scrub: identifier remains visible under supported "
            f"encoding(s): {', '.join(remaining)}"
        )
    if dry_run:
        return n

    before = hashlib.sha256(raw).hexdigest()
    after = hashlib.sha256(scrubbed).hexdigest()
    sidecar = p.with_name(p.name + ".redaction.txt")
    provenance = (
        f"Redaction provenance for {p.name}\n"
        f"original_sha256={before}\n"
        f"stored_sha256={after}\n"
        f"occurrences={n}\n"
        f"encoding={encoding}\n"
        "method=byte-level replacement of the encoded identifier only\n"
        "verification=the written bytes equal the original bytes with only the encoded "
        "identifier spans replaced\n"
        f"replacement=local account/machine identifier -> {REPLACEMENT}\n"
        "scope=Only the encoded identifier bytes were replaced. Every other byte, including any\n"
        "malformed sequences, is unchanged. No measurement, warning count, exit code or\n"
        "conclusion was altered.\n"
    ).encode("utf-8")

    try:
        evidence_mode = stat.S_IMODE(p.stat().st_mode)
    except OSError as e:
        raise RuntimeError(f"cannot stat {p}: {e}") from e

    old_sidecar: bytes | None = None
    old_sidecar_mode: int | None = None
    if sidecar.is_symlink():
        raise RuntimeError(
            f"refusing provenance symlink {sidecar}: replacing it would break repository "
            "structure while leaving the referent unchanged"
        )
    if sidecar.exists():
        try:
            old_sidecar = sidecar.read_bytes()
            old_sidecar_mode = stat.S_IMODE(sidecar.stat().st_mode)
        except OSError as e:
            raise RuntimeError(f"cannot preserve existing provenance {sidecar}: {e}") from e

    evidence_tmp: pathlib.Path | None = None
    sidecar_tmp: pathlib.Path | None = None
    try:
        # Stage BOTH artifacts before publishing either one. A sidecar staging failure therefore
        # cannot occur after evidence bytes have changed.
        evidence_tmp = _stage_bytes(p, scrubbed, evidence_mode)
        # A newly-created provenance sidecar should be publishable wherever the evidence is.
        # mkstemp defaults to 0600, so inherit the evidence mode when there is no prior sidecar.
        sidecar_mode = old_sidecar_mode if old_sidecar_mode is not None else evidence_mode
        sidecar_tmp = _stage_bytes(sidecar, provenance, sidecar_mode)

        # Publish provenance FIRST. A hard interruption after this point can leave a stale
        # sidecar next to the original evidence, but can never leave scrubbed evidence without
        # the original/stored hashes needed to audit it.
        os.replace(sidecar_tmp, sidecar)
        sidecar_tmp = None
        try:
            os.replace(evidence_tmp, p)
            evidence_tmp = None
        except BaseException:
            # Evidence replacement failed, so the original evidence still exists. Restore the
            # prior provenance state so a normal exception path is fully transactional.
            try:
                if old_sidecar is None:
                    sidecar.unlink(missing_ok=True)
                else:
                    restore_tmp = _stage_bytes(sidecar, old_sidecar, old_sidecar_mode)
                    os.replace(restore_tmp, sidecar)
            except BaseException as rollback_error:
                raise RuntimeError(
                    f"evidence update failed and provenance rollback also failed: {rollback_error}"
                )
            raise
    except BaseException as e:
        raise RuntimeError(f"transactional redaction failed for {p}: {e}") from e
    finally:
        for tmp in (evidence_tmp, sidecar_tmp):
            if tmp is not None:
                try:
                    tmp.unlink()
                except OSError:
                    pass

    if _sha256(p) != after:
        raise RuntimeError(f"post-write hash verification failed for {p}")
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

    # No-path mode always scans the CadBridge repository that CONTAINS this tool,
    # never an arbitrary caller working directory or a different Git checkout.
    root = pathlib.Path(__file__).resolve().parent.parent
    target_roots: dict[pathlib.Path, pathlib.Path] = {}
    if args.paths:
        targets: list[pathlib.Path] = []
        missing: list[str] = []
        for raw in args.paths:
            p = pathlib.Path(raw)
            if p.is_symlink():
                targets.append(p)
                target_roots[p] = p.parent
            elif p.is_dir():
                for x in sorted(p.rglob("*")):
                    if x.is_file() or x.is_symlink():
                        targets.append(x)
                        target_roots[x] = p
            elif p.is_file():
                targets.append(p)
                target_roots[p] = p.parent
            else:
                missing.append(raw)
        if missing:
            for item in missing:
                print(f"ERROR: explicit redaction path does not exist: {item}", file=sys.stderr)
            return 2
    else:
        try:
            targets = _tracked_files(root)
            target_roots = {p: root for p in targets}
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

    total = 0
    hit_files: list[str] = []
    undecodable: list[str] = []
    for p in targets:
        try:
            n = scrub_file(
                p,
                args.identifier,
                dry_run=args.check or args.dry_run,
                path_root=target_roots.get(p),
            )
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

    # Every intended target that failed processing is UNVERIFIED and therefore blocks a clean
    # result. UNSCANNABLE_SUFFIXES is enforced inside scrub_file() and deliberately raises;
    # there is no second "skip" list here. Keeping this list identical to undecodable also
    # prevents a stale symbol rename from turning a deliberate refusal into a NameError.
    unexpected = list(undecodable)

    if undecodable:
        print(f"  WARN  {len(undecodable)} file(s) not scanned:")
        for u in undecodable[:20]:
            print(f"          {u}")

    if unexpected:
        mode = "--check" if args.check else ("--dry-run" if args.dry_run else "scrub")
        print(
            f"  FAIL  {len(unexpected)} file(s) failed processing during {mode}. "
            "A redaction workflow may not report success while any intended target was "
            "unreadable, undecodable, or failed its transactional write.",
            file=sys.stderr,
        )
        return 1

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
