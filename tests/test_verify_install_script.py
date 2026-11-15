from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
from contextlib import closing
from importlib.metadata import version
from importlib.resources import files
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
    cli_path = str(Path(module.sys.executable).with_name("insider-scanner-cli"))

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        output = (
            f"insider-scanner-cli {version('insider-scanner')}"
            if command[-1] == "--version"
            else "usage: insider-scanner-cli"
        )
        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr(module.shutil, "which", lambda *args, **kwargs: cli_path)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.check_cli()

    assert calls == [
        (
            [cli_path, "--help"],
            {"check": False, "capture_output": True, "text": True},
        ),
        (
            [cli_path, "--version"],
            {"check": False, "capture_output": True, "text": True},
        ),
    ]


def test_check_cli_rejects_missing_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script()
    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda *args, **kwargs: str(
            Path(module.sys.executable).with_name("insider-scanner-cli")
        ),
    )

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="unexpected output")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="missing usage"):
        module.check_cli()


def test_check_cli_rejects_version_mismatching_installed_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    monkeypatch.setattr(module.shutil, "which", lambda *args, **kwargs: "cli")

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        output = (
            "insider-scanner-cli 0.0.0"
            if command[-1] == "--version"
            else "usage: insider-scanner-cli"
        )
        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="version does not match installed metadata"):
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


def test_check_gui_launcher_executes_installed_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    gui_path = str(Path(module.sys.executable).with_name("insider-scanner"))
    captured: dict[str, object] = {}

    monkeypatch.setattr(module.shutil, "which", lambda *args, **kwargs: gui_path)

    def fake_run(command: list[str], **kwargs: object) -> None:
        captured.update({"command": command, **kwargs})

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.check_gui_launcher()

    assert captured["command"] == [gui_path, "--verify-install"]
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


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ("database", "did not create the local database"),
        ("migration", "did not apply all database migrations"),
        ("seed", "did not copy packaged seed"),
        ("cache", "did not create the isolated cache"),
        ("command", "CLI offline startup failed"),
        (None, None),
    ],
)
def test_check_cli_bootstrap_validates_disposable_runtime(
    monkeypatch: pytest.MonkeyPatch, fault: str | None, message: str | None
) -> None:
    from insider_scanner.persistence.migrations import MIGRATIONS

    module = _load_script()
    calls: list[tuple[list[str], dict[str, object]]] = []
    cli_path = str(Path(module.sys.executable).with_name("insider-scanner-cli"))
    monkeypatch.setenv("PYTHONPATH", "untrusted-checkout")
    monkeypatch.setattr(module.shutil, "which", lambda *args, **kwargs: cli_path)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append((command, kwargs))
        environment = kwargs["env"]
        root = Path(kwargs["cwd"])
        data = Path(environment["INSIDER_SCANNER_DATA_DIR"])
        assert data == root / "data"
        assert Path(environment["INSIDER_SCANNER_CACHE_DIR"]) == root / "cache"
        assert Path(environment["INSIDER_SCANNER_LOG_DIR"]) == root / "logs"
        assert "PYTHONPATH" not in environment
        assert command == [cli_path, "import-legacy", str(root / "empty-legacy.json")]
        assert (root / "empty-legacy.json").read_text(encoding="utf-8") == "[]"
        if fault == "command":
            return subprocess.CompletedProcess(command, 1, stderr="startup failed")
        data.mkdir()
        if fault != "database":
            with closing(
                sqlite3.connect(data / "insider_scanner.sqlite3")
            ) as connection:
                connection.execute("CREATE TABLE schema_version (version INTEGER)")
                versions = MIGRATIONS[:-1] if fault == "migration" else MIGRATIONS
                connection.executemany(
                    "INSERT INTO schema_version VALUES (?)",
                    [(migration.version,) for migration in versions],
                )
                connection.execute("CREATE TABLE us_trades (id INTEGER)")
                connection.commit()
        seeds = files("insider_scanner.resources.seeds")
        for name in (
            "congress_members.json",
            "tickers_watchlist.txt",
            "eu_watchlist.txt",
        ):
            (data / name).write_bytes((seeds / name).read_bytes())
        if fault == "seed":
            (data / "congress_members.json").write_text("{}", encoding="utf-8")
        if fault != "cache":
            (root / "cache" / "edgar").mkdir(parents=True)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    if message is None:
        module.check_cli_bootstrap()
    else:
        with pytest.raises(RuntimeError, match=message):
            module.check_cli_bootstrap()

    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 60
    assert not Path(calls[0][1]["cwd"]).exists()


def test_main_runs_base_checks_without_gui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    calls: list[str] = []

    monkeypatch.setattr(module, "check_cli", lambda: calls.append("cli"))
    monkeypatch.setattr(
        module, "check_cli_bootstrap", lambda: calls.append("bootstrap"), raising=False
    )
    monkeypatch.setattr(module, "check_resources", lambda: calls.append("resources"))
    monkeypatch.setattr(module, "check_gui_import", lambda: calls.append("gui"))
    monkeypatch.setattr(module, "check_gui_launcher", lambda: calls.append("launcher"))

    module.main(["--mode", "base"])

    assert calls == ["cli", "bootstrap", "resources"]


def test_main_runs_full_checks_with_gui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    calls: list[str] = []

    monkeypatch.setattr(module, "check_cli", lambda: calls.append("cli"))
    monkeypatch.setattr(
        module, "check_cli_bootstrap", lambda: calls.append("bootstrap"), raising=False
    )
    monkeypatch.setattr(module, "check_resources", lambda: calls.append("resources"))
    monkeypatch.setattr(module, "check_gui_import", lambda: calls.append("gui"))
    monkeypatch.setattr(module, "check_gui_launcher", lambda: calls.append("launcher"))

    module.main(["--mode", "gui"])

    assert calls == ["cli", "bootstrap", "resources", "gui", "launcher"]
