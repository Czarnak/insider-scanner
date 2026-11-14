"""Ensure the legacy typing baseline still rejects new error categories."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_typing_baseline_checks_new_modules_and_untyped_bodies(tmp_path):
    package = tmp_path / "insider_scanner"
    core = package / "core"
    core.mkdir(parents=True)
    (package / "__init__.py").touch()
    (core / "__init__.py").touch()
    # This legacy module permits its existing arg-type category, but must still
    # reject an unrelated return-value error.
    (core / "openinsider.py").write_text(
        "def accepts_int(value: int) -> None: ...\n"
        "accepts_int('existing legacy category')\n"
        "def answer() -> int:\n"
        "    return 'new category'\n",
        encoding="utf-8",
    )
    # The same arg-type category must fail in a new module, even in the body of
    # an untyped function.
    (package / "new_module.py").write_text(
        "def accepts_int(value: int) -> None: ...\n"
        "def untyped():\n"
        "    accepts_int('new regression')\n",
        encoding="utf-8",
    )

    config = Path(__file__).resolve().parents[1] / "mypy.ini"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(config),
            "--no-incremental",
            str(package),
        ],
        check=False,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    errors = [line for line in result.stdout.splitlines() if ": error:" in line]
    assert len(errors) == 2, result.stdout
    assert any("openinsider.py" in line and "[return-value]" in line for line in errors)
    assert any("new_module.py" in line and "[arg-type]" in line for line in errors)
