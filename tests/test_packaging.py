"""Regression tests for distributable package contents."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
import venv
from pathlib import Path
from zipfile import ZipFile

import pytest

EXPECTED_SEEDS = {
    "insider_scanner/resources/seeds/congress_members.json",
    "insider_scanner/resources/seeds/tickers_watchlist.txt",
    "insider_scanner/resources/seeds/eu_watchlist.txt",
}

EXPECTED_FONTS = {
    "insider_scanner/resources/fonts/Inter-Regular.ttf",
    "insider_scanner/resources/fonts/JetBrainsMono-Regular.ttf",
}


def _copy_build_source(project_root: Path, destination: Path) -> None:
    """Copy distribution inputs without old build outputs or local state."""
    destination.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(project_root / name, destination / name)
    shutil.copytree(
        project_root / "src",
        destination / "src",
        ignore=shutil.ignore_patterns("*.egg-info", "__pycache__", "*.pyc"),
    )


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    build_root = tmp_path_factory.mktemp("package-build")
    source_root = build_root / "source"
    _copy_build_source(Path(__file__).resolve().parents[1], source_root)
    output_root = build_root / "dist"
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                "--outdir",
                str(output_root),
            ],
            cwd=source_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Build failed!\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}"
        ) from e
    wheels = list(output_root.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def test_built_wheel_contains_all_packaged_seeds(built_wheel: Path):
    with ZipFile(built_wheel) as wheel:
        assert EXPECTED_SEEDS <= set(wheel.namelist())


def test_built_wheel_contains_bundled_fonts(built_wheel: Path):
    with ZipFile(built_wheel) as wheel:
        assert EXPECTED_FONTS <= set(wheel.namelist())


def test_build_source_excludes_old_artifacts_and_user_data(tmp_path: Path) -> None:
    source = tmp_path / "checkout"
    source.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        (source / name).write_text(name, encoding="utf-8")
    package = source / "src" / "insider_scanner"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    for relative in (
        "build/lib/insider_scanner/deleted.py",
        "src/insider_scanner.egg-info/SOURCES.txt",
        "src/insider_scanner/__pycache__/obsolete.pyc",
        "data/private.sqlite3",
        ".env",
    ):
        stale = source / relative
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("excluded", encoding="utf-8")
    destination = tmp_path / "isolated-source"

    _copy_build_source(source, destination)

    assert {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    } == {
        "pyproject.toml",
        "README.md",
        "LICENSE",
        "src/insider_scanner/__init__.py",
    }


def test_installed_wheel_bootstraps_offline(built_wheel: Path, tmp_path: Path) -> None:
    environment_root = tmp_path / "installed"
    venv.EnvBuilder(with_pip=True).create(environment_root)
    scripts = environment_root / ("Scripts" if os.name == "nt" else "bin")
    interpreter = scripts / ("python.exe" if os.name == "nt" else "python")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    installed_site = subprocess.run(
        [
            str(interpreter),
            "-I",
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=environment,
    )
    # Reuse already installed test dependencies without processing the host's
    # editable-install .pth files or contacting a package index.
    (Path(installed_site.stdout.strip()) / "test-dependencies.pth").write_text(
        sysconfig.get_path("purelib") + "\n", encoding="utf-8"
    )
    subprocess.run(
        [
            str(interpreter),
            "-I",
            "-m",
            "pip",
            "--disable-pip-version-check",
            "install",
            "--no-index",
            "--no-deps",
            "--ignore-installed",
            str(built_wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=environment,
    )
    # The application must resolve from the wheel installed into this venv.
    subprocess.run(
        [
            str(interpreter),
            "-I",
            "-c",
            (
                "from pathlib import Path; import sys, insider_scanner; "
                "assert Path(insider_scanner.__file__).resolve().is_relative_to("
                "Path(sys.prefix).resolve()), insider_scanner.__file__"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=environment,
    )
    script = Path(__file__).resolve().parents[1] / "scripts" / "verify_install.py"
    result = subprocess.run(
        [str(interpreter), "-I", str(script), "--mode", "base"],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=environment,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "CLI offline startup, migrations and seed copying" in result.stdout
