#!/usr/bin/env python3
"""Run the Congress updater with the active interpreter and an Open States key.

OPENSTATES_API_KEY takes precedence. For compatibility, the ignored key file
beside this wrapper is used only when the environment variable is absent.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    script_dir = Path(__file__).resolve().parent
    environment = os.environ.copy()
    key = environment.get("OPENSTATES_API_KEY", "").strip()
    if not key:
        try:
            key = (script_dir / "api_key.txt").read_text(encoding="utf-8").strip()
        except OSError:
            print(
                "ERROR: Set OPENSTATES_API_KEY or provide the local scripts/api_key.txt file.",
                file=sys.stderr,
            )
            return 1
    if not key:
        print("ERROR: The Open States API key is empty.", file=sys.stderr)
        return 1
    environment["OPENSTATES_API_KEY"] = key
    arguments = sys.argv[1:] if argv is None else argv
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(script_dir / "update_congress.py"),
                "--include-state",
                *arguments,
            ],
            env=environment,
            check=False,
        )
    except OSError:
        print("ERROR: Could not start the Congress updater.", file=sys.stderr)
        return 1
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
