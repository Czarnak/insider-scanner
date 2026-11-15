"""Durable application diagnostics, independent of a console session."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from insider_scanner.utils import logging as app_logging


@pytest.fixture(autouse=True)
def isolated_logging(monkeypatch, tmp_path):
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    monkeypatch.setenv("INSIDER_SCANNER_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("INSIDER_SCANNER_LOG_LEVEL", raising=False)
    yield
    for handler in root.handlers[:]:
        if handler not in original_handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(original_level)


def test_logs_persist_with_date_and_actionable_location(tmp_path):
    destination = app_logging.setup_logging()
    app_logging.get_logger("test").warning(
        "filing failed accession=0001234567-26-000001"
    )

    assert destination == tmp_path / "logs" / "insider-scanner.log"
    output = destination.read_text(encoding="utf-8")
    assert "filing failed accession=0001234567-26-000001" in output
    # The timestamp must remain unambiguous across midnight and resumed runs.
    assert output[:10].count("-") == 2
    assert output[10] == " "
    assert str(destination) in app_logging.diagnostic_hint()


def test_persistent_diagnostics_keep_internal_details_off_stderr(capsys):
    destination = app_logging.setup_logging()
    try:
        raise RuntimeError("internal database details")
    except RuntimeError:
        app_logging.get_logger("test").exception("Command failed")
    assert capsys.readouterr().err == ""
    assert "internal database details" in destination.read_text(encoding="utf-8")


def test_rotates_logs_and_retains_previous_diagnostics(tmp_path):
    destination = app_logging.setup_logging()
    handler = next(
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, RotatingFileHandler)
    )
    assert handler.maxBytes == 5 * 1024 * 1024
    assert handler.backupCount == 5
    handler.maxBytes = 180
    logger = app_logging.get_logger("rotation")
    logger.warning("first diagnostic " + "x" * 70)
    logger.warning("second diagnostic " + "x" * 70)

    assert "second diagnostic" in destination.read_text(encoding="utf-8")
    assert "first diagnostic" in Path(str(destination) + ".1").read_text(
        encoding="utf-8"
    )


def test_reconfiguration_avoids_duplicate_records_and_honors_level(monkeypatch):
    monkeypatch.setenv("INSIDER_SCANNER_LOG_LEVEL", "DEBUG")
    destination = app_logging.setup_logging()
    app_logging.setup_logging()
    logger = app_logging.get_logger("level")
    logger.debug("one debug record")

    assert destination.read_text(encoding="utf-8").count("one debug record") == 1
    app_logging.setup_logging("ERROR")
    logger.warning("hidden warning")
    logger.error("visible error")
    output = destination.read_text(encoding="utf-8")
    assert "hidden warning" not in output
    assert "visible error" in output
    assert logging.getLogger("urllib3").level == logging.WARNING


def test_unwritable_log_directory_retains_console_diagnostics(
    monkeypatch, tmp_path, capsys
):
    invalid_directory = tmp_path / "ordinary-file"
    invalid_directory.write_text("keep", encoding="utf-8")
    monkeypatch.setenv("INSIDER_SCANNER_LOG_DIR", str(invalid_directory))

    assert app_logging.setup_logging() is None
    app_logging.get_logger("fallback").warning("retained console diagnostic")

    output = capsys.readouterr().err
    assert "retained console diagnostic" in output
    assert "logging to stderr" in output
    assert app_logging.get_log_file() is None
    assert "stderr" in app_logging.diagnostic_hint()
    assert invalid_directory.read_text(encoding="utf-8") == "keep"


def test_data_override_isolates_logs_from_platform_profile(monkeypatch, tmp_path):
    monkeypatch.delenv("INSIDER_SCANNER_LOG_DIR")
    monkeypatch.setenv("INSIDER_SCANNER_DATA_DIR", str(tmp_path / "profile"))

    assert app_logging.setup_logging() == tmp_path / "profile/logs/insider-scanner.log"


def test_default_logs_use_platform_log_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("INSIDER_SCANNER_LOG_DIR")
    monkeypatch.delenv("INSIDER_SCANNER_DATA_DIR")
    monkeypatch.setattr(
        app_logging,
        "PlatformDirs",
        lambda *args: type("Dirs", (), {"user_log_path": tmp_path / "platform-logs"})(),
    )

    assert app_logging.setup_logging() == tmp_path / "platform-logs/insider-scanner.log"


def test_invalid_environment_level_falls_back_without_losing_diagnostics(
    monkeypatch, capsys
):
    monkeypatch.setenv("INSIDER_SCANNER_LOG_LEVEL", "not-a-level")

    destination = app_logging.setup_logging()
    app_logging.get_logger("fallback").warning("retained warning")

    assert "INSIDER_SCANNER_LOG_LEVEL" in capsys.readouterr().err
    assert "retained warning" in destination.read_text(encoding="utf-8")
