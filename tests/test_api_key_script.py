"""The compatibility updater wrapper preserves interpreter, paths and failures."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def wrapper(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts" / "api_key.py"
    spec = importlib.util.spec_from_file_location("api_key_wrapper", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fake_scripts = tmp_path / "scripts"
    fake_scripts.mkdir()
    monkeypatch.setattr(module, "__file__", str(fake_scripts / "api_key.py"))
    return module, fake_scripts


def test_wrapper_uses_own_paths_and_active_python_from_any_cwd(
    wrapper, tmp_path, monkeypatch
):
    module, scripts = wrapper
    (scripts / "api_key.txt").write_text("local-test-key\n", encoding="utf-8")
    monkeypatch.delenv("OPENSTATES_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main(["--dry-run"]) == 7
    command, kwargs = calls[0]
    assert command == [
        sys.executable,
        str(scripts / "update_congress.py"),
        "--include-state",
        "--dry-run",
    ]
    assert kwargs["env"]["OPENSTATES_API_KEY"] == "local-test-key"
    assert kwargs["check"] is False
    assert "shell" not in kwargs


def test_wrapper_prefers_environment_without_reading_local_key(wrapper, monkeypatch):
    module, _scripts = wrapper
    monkeypatch.setenv("OPENSTATES_API_KEY", "environment-test-key")

    def run(_command, **kwargs):
        assert kwargs["env"]["OPENSTATES_API_KEY"] == "environment-test-key"
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main([]) == 0


def test_missing_key_fails_without_starting_child(wrapper, monkeypatch, capsys):
    module, _scripts = wrapper
    monkeypatch.delenv("OPENSTATES_API_KEY", raising=False)

    def unexpected(*_args, **_kwargs):
        pytest.fail("Child must not run without a key")

    monkeypatch.setattr(module.subprocess, "run", unexpected)
    assert module.main([]) == 1
    assert "OPENSTATES_API_KEY" in capsys.readouterr().err
