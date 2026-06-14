import tomllib
from pathlib import Path


PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _load() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_distribution_name_is_insider_scanner():
    assert _load()["project"]["name"] == "insider-scanner"


def test_console_and_gui_scripts_present():
    project = _load()["project"]
    assert project["scripts"] == {
        "insider-scanner": "insider_scanner.main:main",
        "insider-scanner-cli": "insider_scanner.cli:main",
    }
    assert "gui-scripts" not in project


def test_requires_python_floor():
    assert _load()["project"]["requires-python"] == ">=3.11"


def test_has_python_version_classifiers():
    classifiers = _load()["project"]["classifiers"]
    for version in ("3.11", "3.12", "3.13", "3.14"):
        assert any(f"Python :: {version}" in classifier for classifier in classifiers)


def test_uses_spdx_license_metadata():
    project = _load()["project"]
    assert project["license"] == "MIT"
    assert not any(
        classifier.startswith("License ::") for classifier in project["classifiers"]
    )


def test_has_project_urls():
    urls = _load()["project"]["urls"]
    assert "Repository" in urls
    assert "insider-scanner" in urls["Repository"]


def test_core_runtime_deps_are_bounded():
    dependencies = _load()["project"]["dependencies"]
    joined = " ".join(dependencies).lower()
    for dependency_name in ("curl-cffi", "sqlalchemy"):
        assert dependency_name in joined
    assert "lxml>=4.9,<7" in dependencies
    assert "pyside6" not in joined
    assert "pyqtgraph" not in joined
    assert all(
        "~=" in dependency or (">=" in dependency and "<" in dependency)
        for dependency in dependencies
    )


def test_gui_dependencies_are_isolated_in_gui_extra():
    optional_dependencies = _load()["project"]["optional-dependencies"]
    gui_dependencies = " ".join(optional_dependencies["gui"]).lower()

    assert "pyside6" in gui_dependencies
    assert "pyqtgraph" in gui_dependencies
    assert all(
        "~=" in dependency or (">=" in dependency and "<" in dependency)
        for dependency in optional_dependencies["gui"]
    )

    other_extras = " ".join(
        dependency
        for extra_name, dependencies in optional_dependencies.items()
        if extra_name != "gui"
        for dependency in dependencies
    ).lower()
    assert "pyside6" not in other_extras
    assert "pyqtgraph" not in other_extras
