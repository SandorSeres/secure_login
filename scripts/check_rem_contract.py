#!/usr/bin/env python3
"""Fail-closed source compatibility check for the REM zero-instance platform.

This is a lightweight negative gate, NOT proof of behavior or authorization:
passing requires separate isolated IAM/DB/session end-to-end verification.
"""
from pathlib import Path
import argparse
import json
import sys

REQUIRED = {
    "api/app/config.py": ("DATABASE_URL", "PORTALS_JSON", "ROLES_JSON", "ADMIN_USERNAMES"),
    "api/app/main.py": ('"/health"', '"/auth/resolve"'),
}

def check(root: Path) -> list[str]:
    failures = []
    for relative, needles in REQUIRED.items():
        file = root / relative
        if not file.is_file():
            failures.append(f"{relative}: missing source file")
            continue
        source = file.read_text(encoding="utf-8")
        for needle in needles:
            if needle not in source:
                failures.append(f"{relative}: REM contract marker missing: {needle}")
    return failures

def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    failures = check(args.source_root)
    print(json.dumps({"rem_contract_markers_present": not failures,
                      "failures": failures,
                      "warning": "Static markers alone never certify IAM or runtime compatibility"},
                     indent=2))
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
