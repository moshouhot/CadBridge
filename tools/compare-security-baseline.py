#!/usr/bin/env python3
"""Compare two AutoCAD user-profile security baselines captured by capture-cad-security-baseline.ps1.

Reports, per profile key:
  * profiles added / removed
  * changes to SECURELOAD / TRUSTEDPATHS / LISPSYS / other watched variables

TRUSTEDPATHS values are long; by default only the SET of path entries is compared and the
newly added / removed entries are listed, which is what actually matters for attribution.

Usage: compare-security-baseline.py <before.json> <after.json> [--full]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WATCHED = ("SECURELOAD", "TRUSTEDPATHS", "LISPSYS", "LOADCTRLS", "ACADLSPASDOC",
           "DEMANDLOAD", "TRUSTEDDOMAINS", "AUTOLOAD")


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def normalise_key(k: str) -> str:
    # PowerShell may prefix keys with the provider path; strip it so both sides match.
    return k.replace("Microsoft.PowerShell.Core\\Registry::", "")


def entries(value) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {p for p in value.split(";") if p}
    return {str(value)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--full", action="store_true", help="print full values, not just set diffs")
    args = ap.parse_args()

    before, after = load(args.before), load(args.after)
    b = {normalise_key(p["variables_key"]): p["values"] for p in before.get("profiles_with_values", [])}
    a = {normalise_key(p["variables_key"]): p["values"] for p in after.get("profiles_with_values", [])}

    print(f"profiles with values: before={len(b)} after={len(a)}")
    added = sorted(set(a) - set(b))
    removed = sorted(set(b) - set(a))
    for k in added:
        print(f"  PROFILE ADDED   {k}")
    for k in removed:
        print(f"  PROFILE REMOVED {k}")

    changed = 0
    for key in sorted(set(b) & set(a)):
        for var in WATCHED:
            bv, av = b[key].get(var), a[key].get(var)
            if bv == av:
                continue
            changed += 1
            print(f"\nCHANGED {var}")
            print(f"  profile: {key}")
            if var == "TRUSTEDPATHS":
                be, ae = entries(bv), entries(av)
                print(f"  entries: {len(be)} -> {len(ae)}")
                for e in sorted(ae - be):
                    print(f"    + {e}")
                for e in sorted(be - ae):
                    print(f"    - {e}")
                if args.full:
                    print(f"  before: {bv!r}\n  after:  {av!r}")
            else:
                print(f"  before: {bv!r}")
                print(f"  after:  {av!r}")

    print()
    if changed == 0:
        print("RESULT: no observed change to any watched variable")
    else:
        print(f"RESULT: {changed} watched-variable change(s); attribute each one explicitly "
              f"before using it as evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
