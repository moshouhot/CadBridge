#!/usr/bin/env python3
"""Convert between Windows ('F:\\dir\\file') and MSYS ('/f/dir/file') path spellings.

Git-Bash on Windows needs the MSYS spelling for its own file tests, while native Windows
tools (accoreconsole.exe, dotnet, python.exe) need the Windows spelling. Guessing wrong
silently loses evidence, so conversion is done in one place.

Usage:
  pathconv.py to-msys  "F:\\a\\b"     -> /f/a/b
  pathconv.py to-win   "/f/a/b"       -> F:\\a\\b
  pathconv.py to-win-slash "/f/a/b"   -> F:/a/b
"""
from __future__ import annotations

import sys


def to_win(p: str) -> str:
    if len(p) >= 2 and p[1] == ":":
        return p.replace("/", "\\")
    if p.startswith("/") and len(p) > 2 and p[2] == "/":
        # /f/rest  ->  F:\rest
        return (p[1].upper() + ":" + p[2:]).replace("/", "\\")
    return p.replace("/", "\\")


def to_win_slash(p: str) -> str:
    return to_win(p).replace("\\", "/")


def to_msys(p: str) -> str:
    if len(p) >= 2 and p[1] == ":":
        return "/" + p[0].lower() + p[2:].replace("\\", "/")
    return p.replace("\\", "/")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    mode, path = sys.argv[1], sys.argv[2]
    fn = {"to-msys": to_msys, "to-win": to_win, "to-win-slash": to_win_slash}.get(mode)
    if fn is None:
        print(f"unknown mode: {mode}", file=sys.stderr)
        return 2
    sys.stdout.write(fn(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
