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
import os
import stat
import sys
import tempfile
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


def validate_tests(tests: list[dict], artifact_paths: set[str]) -> list[str]:
    """Validate evidence references against artifacts actually hashed into this run."""
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
            if not isinstance(rel, str) or not rel:
                problems.append(f"test {tid}: evidence path must be a non-empty string: {rel!r}")
                continue
            candidate = Path(rel)
            if candidate.is_absolute() or ".." in candidate.parts:
                problems.append(
                    f"test {tid}: evidence path must stay inside the run directory: {rel!r}"
                )
                continue
            normalized = candidate.as_posix()
            if normalized not in artifact_paths:
                problems.append(
                    f"test {tid}: evidence path is not a hashed manifest artifact: {rel}"
                )
    return problems


def validate_redactions(redactions: object, artifacts: list[dict]) -> list[str]:
    """Cross-check preserved redaction provenance against freshly hashed stored bytes."""
    problems: list[str] = []
    if not isinstance(redactions, dict):
        return ["redactions provenance must be a JSON object"]
    entries = redactions.get("artifacts", [])
    if not isinstance(entries, list):
        return ["redactions.artifacts must be a JSON array"]

    by_path = {
        a.get("path"): a
        for a in artifacts
        if isinstance(a, dict) and isinstance(a.get("path"), str)
    }
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            problems.append(f"redactions.artifacts[{i}] must be a JSON object")
            continue
        path = entry.get("path")
        stored_sha = entry.get("stored_sha256")
        if not isinstance(path, str) or not path:
            problems.append(f"redactions.artifacts[{i}].path must be a non-empty string")
            continue
        if not isinstance(stored_sha, str) or not stored_sha:
            problems.append(f"redaction {path}: stored_sha256 is missing")
            continue
        current = by_path.get(Path(path).as_posix())
        if current is None:
            problems.append(
                f"redaction {path}: carried provenance refers to an artifact not present "
                "in the freshly hashed manifest"
            )
            continue
        if current.get("sha256") != stored_sha:
            problems.append(
                f"redaction {path}: stored_sha256 {stored_sha} does not match freshly hashed "
                f"artifact sha256 {current.get('sha256')}"
            )
        if "stored_bytes" in entry and current.get("bytes") != entry.get("stored_bytes"):
            problems.append(
                f"redaction {path}: stored_bytes {entry.get('stored_bytes')} does not match "
                f"fresh artifact size {current.get('bytes')}"
            )
    return problems


def write_manifest_atomically(out: Path, manifest: dict) -> None:
    """Write the manifest via a temp file + atomic replace.

    WHY ATOMIC: the manifest is the artifact that makes the rest of the evidence checkable. A
    direct write_text() truncates the destination first, so an interruption (disk full,
    Ctrl-C, a crash) leaves a PARTIAL manifest in place. A truncated manifest is worse than no
    manifest at all: it still parses as JSON in many cases and still looks like evidence,
    while the hashes it should guarantee are silently wrong or absent.

    os.replace() is atomic on the same filesystem, so a reader sees either the previous
    complete manifest or the new complete one, never a half-written file.

    WHY LINE ENDINGS ARE PRESERVED: docs/evidence/** is marked -text in .gitattributes, so
    manifest bytes are stored exactly as written. The existing P0/P1 manifests use CRLF while
    newer ones use LF. Writing LF unconditionally rewrote a 273-line CRLF manifest as LF and
    produced a whole-file diff with no content change, which makes every manifest edit
    unreviewable. The existing convention is therefore kept.
    """
    payload = json.dumps(manifest, indent=2, ensure_ascii=False)

    newline = "\n"
    if out.is_file():
        try:
            existing = out.read_bytes()
        except OSError:
            existing = b""
        # CRLF if the file uses CRLF more often than bare LF, else LF.
        crlf = existing.count(b"\r\n")
        bare_lf = existing.count(b"\n") - crlf
        if crlf > bare_lf:
            newline = "\r\n"

    # Keep the temp file in the same directory so the replace cannot cross a filesystem
    # boundary (which would make it a copy + delete instead of an atomic rename).
    previous_mode: int | None = None
    if out.exists():
        try:
            previous_mode = stat.S_IMODE(out.stat().st_mode)
        except OSError as e:
            raise RuntimeError(f"cannot stat existing manifest {out}: {e}") from e

    fd, tmp_name = tempfile.mkstemp(dir=str(out.parent), prefix=MANIFEST_NAME + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(payload.replace("\n", newline) if newline != "\n" else payload)
            fh.flush()
            os.fsync(fh.fileno())
        if previous_mode is not None:
            os.chmod(tmp_name, previous_mode)
        os.replace(tmp_name, out)
    except BaseException:
        # Never leave a temp file behind, and never leave the previous manifest damaged.
        # If the cleanup ITSELF fails, that must be reported rather than swallowed: silently
        # discarding it would leave manifest.json.*.tmp files behind while the function claims
        # the "no temp file on failure" invariant, and repeated failures would accumulate
        # stale temp files. The original exception still propagates; the cleanup failure is
        # reported alongside it.
        try:
            os.unlink(tmp_name)
        except OSError as cleanup_error:
            print(
                f"ERROR: could not remove temporary file {tmp_name}: {cleanup_error}. "
                f"The write failed AND its cleanup failed, so a stale temp file remains.",
                file=sys.stderr,
            )
        raise


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

    out = root / MANIFEST_NAME
    # PRESERVE NON-DERIVED CONTENT.
    #
    # WHY: this tool regenerates `artifacts` (hashes) and `validation` (fresh problems). But
    # a manifest also carries blocks that are NOT derivable from the directory: the recorded
    # test results, and the publication `redactions` provenance block. Re-running the tool
    # without --status-file used to silently DROP them -- observed in practice: a regeneration
    # reduced a 13-test manifest to 0 tests and erased the redaction provenance, while still
    # printing "validation OK".
    #
    # A manifest that EXISTS but cannot be read or parsed is an ERROR, not an empty starting
    # point. Treating it as {} would discard the non-derived blocks and re-create exactly the
    # data-loss defect this preservation logic exists to prevent -- and a truncated or corrupt
    # manifest is precisely the situation where those blocks are most valuable. The file is
    # left untouched and the tool exits non-zero.
    previous: dict = {}
    if out.is_file():
        try:
            previous = json.loads(out.read_text(encoding="utf-8"))
        except OSError as e:
            print(f"ERROR: existing manifest {out} cannot be read: {e}. Refusing to overwrite "
                  f"it, because its non-derived blocks (tests, redactions) would be lost.",
                  file=sys.stderr)
            return 2
        except json.JSONDecodeError as e:
            print(f"ERROR: existing manifest {out} is not valid JSON: {e}. Refusing to "
                  f"overwrite it, because its non-derived blocks (tests, redactions) would be "
                  f"lost. Repair or remove it deliberately.", file=sys.stderr)
            return 2
        if not isinstance(previous, dict):
            print(f"ERROR: existing manifest {out} is not a JSON object; refusing to overwrite.",
                  file=sys.stderr)
            return 2

    tests: list[dict] = []
    if args.status_file:
        sf = Path(args.status_file)
        if sf.is_file():
            try:
                tests = json.loads(sf.read_text(encoding="utf-8")).get("tests", [])
            except json.JSONDecodeError as e:
                problems.append(f"status file is not valid JSON: {e}")
    elif previous.get("tests"):
        # No status file supplied: keep what was recorded rather than erasing it.
        tests = previous["tests"]

    if not args.no_validate:
        problems += validate_tests(tests, {a["path"] for a in artifacts})

    # SELF-CONSISTENCY: `artifact_count` must equal the array length. Nothing checked this, so
    # a manifest could declare 16 artifacts while listing 15 -- observed in practice after a
    # hand edit that updated `artifacts` without `artifact_count`. A consumer that trusts the
    # declared count to verify completeness would silently accept an inconsistent manifest, and
    # the in-file validation reported ok. Derived first, then asserted.
    artifact_count = len(artifacts)

    manifest = {
        "phase": args.phase,
        "run_id": args.run_id,
        "generated_at_utc": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_dir": str(root),
        "artifact_count": artifact_count,
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
            extra = json.loads(ep.read_text(encoding="utf-8"))
            if not isinstance(extra, dict):
                problems.append("--extra must contain a JSON object")
            else:
                # These fields are derived from the run or validated inputs. Allowing an
                # arbitrary metadata file to overwrite them would let --extra replace the
                # hashed artifact list or turn validation.ok back to true.
                protected = {
                    "phase", "run_id", "generated_at_utc", "run_dir",
                    "artifact_count", "artifacts", "tests", "validation",
                    "redactions",
                }
                for key, value in extra.items():
                    if key in protected:
                        problems.append(
                            f"--extra may not override protected manifest field {key!r}"
                        )
                        continue
                    manifest[key] = value
                manifest["validation"] = {"problems": problems, "ok": not problems}

    # Carry forward provenance blocks that this tool cannot derive. `redactions` in
    # particular documents that published bytes differ from the originally captured ones;
    # losing it would make the published hashes look unexplained.
    if "redactions" in previous and "redactions" not in manifest:
        carried_redactions = previous["redactions"]
        if not args.no_validate:
            problems += validate_redactions(carried_redactions, artifacts)
        manifest["redactions"] = carried_redactions
        manifest.setdefault("notes", [])
        if not any("REDACTION" in n for n in manifest["notes"]):
            manifest["notes"].append(
                "REDACTION: preserved from the previous manifest; some artifact hashes cover "
                "bytes that differ from the originally captured ones. Preserved provenance is "
                "validated against the freshly hashed stored bytes on regeneration."
            )

    # Final self-consistency gate, AFTER --extra and the carried-forward blocks, so a value
    # injected by --extra cannot silently contradict the array it describes. The DERIVED count
    # wins: the written file is always internally consistent, and the tampering is recorded as
    # a validation problem rather than left in the file for a consumer to trip over.
    if manifest.get("artifact_count") != len(manifest.get("artifacts", [])):
        problems.append(
            f"artifact_count {manifest.get('artifact_count')!r} does not match the artifacts "
            f"array length {len(manifest.get('artifacts', []))}; using the derived value"
        )
        manifest["artifact_count"] = len(manifest.get("artifacts", []))
        manifest["validation"] = {"problems": problems, "ok": False}

    # Recompute validation LAST. This covers non-object --extra input too; previously that
    # path appended a problem after validation.ok had already been captured as true.
    manifest["validation"] = {"problems": problems, "ok": not problems}

    write_manifest_atomically(out, manifest)
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
