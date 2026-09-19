#!/usr/bin/env python3
"""Decode accoreconsole.exe captured output into readable UTF-8 text.

accoreconsole writes UTF-16LE when stdout is not a console, and may additionally divert
stdout to a temp file. This normalises whatever was captured into UTF-8 so evidence files
are diffable and greppable.

Usage: decode-accoreconsole-output.py <raw> <out.txt>
"""
from __future__ import annotations

import pathlib
import sys


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    raw_path = pathlib.Path(sys.argv[1])
    out_path = pathlib.Path(sys.argv[2])
    raw = raw_path.read_bytes()

    if raw.count(b"\x00") > len(raw) // 4:
        text = raw.decode("utf-16-le", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")

    text = text.replace("\x00", "")
    out_path.write_text(text, encoding="utf-8")
    print(f"decoded {len(raw)} bytes -> {len(text)} chars -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
