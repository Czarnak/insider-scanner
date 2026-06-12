from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_install.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_install", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verify_install_script_exists() -> None:
    assert SCRIPT_PATH.is_file()


def test_check_cli_validates_help_output(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script()
    calls: list[tuple[list[str], dict[str, object]]] = []
    cli_path = str(Path(module.sys.executable).with_name("insider-scan-cli"))

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="usage: insider-scan-cli")

    monkeypatch.setattr(module.shutil, "which", lambda *args, **kwargs: cli_path)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.check_cli()

    assert calls == [
        (
            [cli_path, "--help"],
            {"capture_output": True, "text": True},
        )
    ]


def test_check_cli_rejects_missing_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script()
    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda *args, **kwargs: str(
            Path(module.sys.executable).with_name("insider-scan-cli")
        ),
    )

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="unexpected output")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="missing usage"):
        module.check_cli()


def test_check_gui_import_uses_headless_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> None:
        captured.update({"command": command, **kwargs})

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.check_gui_import()

    assert captured["command"] == [
        module.sys.executable,
        "-c",
        "import insider_scanner.gui.main_window; print('gui import ok')",
    ]
    assert captured["check"] is True
    assert captured["env"]["QT_QPA_PLATFORM"] == "offscreen"


def test_check_resources_uses_packaged_seed_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> None:
        captured.update({"command": command, **kwargs})

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.check_resources()

    command = captured["command"]
    assert command[:2] == [module.sys.executable, "-c"]
    assert "insider_scanner.resources.seeds" in command[2]
    assert "congress_members.json" in command[2]
    assert "tickers_watchlist.txt" in command[2]
    assert "eu_watchlist.txt" in command[2]
    assert "assert " not in command[2]
    assert "raise SystemExit" in command[2]
    assert captured["check"] is True
