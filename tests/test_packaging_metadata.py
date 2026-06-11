import tomllib
from pathlib import Path


PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _load() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_distribution_name_is_insider_scan():
    assert _load()["project"]["name"] == "insider-scan"


def test_console_scripts_present():
    scripts = _load()["project"]["scripts"]
    assert scripts["insider-scan"] == "insider_scanner.main:main"
    assert scripts["insider-scan-cli"] == "insider_scanner.cli:main"


def test_requires_python_floor():
    assert _load()["project"]["requires-python"] == ">=3.11"


def test_has_python_version_classifiers():
    classifiers = _load()["project"]["classifiers"]
    for version in ("3.11", "3.12", "3.13"):
        assert any(f"Python :: {version}" in classifier for classifier in classifiers)
    assert any(
        "License :: OSI Approved :: MIT" in classifier for classifier in classifiers
    )


def test_has_project_urls():
    urls = _load()["project"]["urls"]
    assert "Repository" in urls
    assert "insider-scan" in urls["Repository"]


def test_core_runtime_deps_are_bounded():
    dependencies = _load()["project"]["dependencies"]
    joined = " ".join(dependencies)
    for dependency_name in ("curl-cffi", "pyqtgraph", "SQLAlchemy"):
        assert dependency_name in joined
    assert all(
        "~=" in dependency or (">=" in dependency and "<" in dependency)
        for dependency in dependencies
    )
