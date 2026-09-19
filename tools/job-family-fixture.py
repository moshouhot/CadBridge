#!/usr/bin/env python3
"""Harmless fixture for safe_process --job-self-test."""
from __future__ import annotations

import pathlib
import subprocess
import sys
import time


def main() -> int:
    if "--child" in sys.argv:
        time.sleep(60)
        return 0
    child = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).resolve()), "--child"])
    print(child.pid, flush=True)
    time.sleep(60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
