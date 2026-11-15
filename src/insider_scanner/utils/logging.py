"""Logging configuration."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from platformdirs import PlatformDirs

from insider_scanner.utils.config import APP_AUTHOR, APP_NAME


class _ConsoleHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        # Embedded callers and tests can replace stderr between commands.
        self.stream = sys.stderr
        super().emit(record)


class _LogFileHandler(RotatingFileHandler):
    pass


def _log_path() -> Path:
    log_override = os.getenv("INSIDER_SCANNER_LOG_DIR")
    data_override = os.getenv("INSIDER_SCANNER_DATA_DIR")
    if log_override:
        directory = Path(log_override).expanduser()
    elif data_override:
        # A disposable runtime profile must also isolate its diagnostics.
        directory = Path(data_override).expanduser() / "logs"
    else:
        directory = PlatformDirs(APP_NAME, APP_AUTHOR).user_log_path
    return directory / "insider-scanner.log"


def shutdown_logging() -> None:
    """Release only the handlers installed by this application."""
    root = logging.getLogger()
    for handler in root.handlers[:]:
        if isinstance(handler, (_ConsoleHandler, _LogFileHandler)):
            root.removeHandler(handler)
            handler.close()


def setup_logging(level: int | str | None = None) -> Path | None:
    """Configure console and rotating diagnostics; retain console on I/O failure."""
    selected = (
        level if level is not None else os.getenv("INSIDER_SCANNER_LOG_LEVEL", "INFO")
    )
    invalid_environment_level = False
    if isinstance(selected, str):
        numeric_level = logging.getLevelNamesMapping().get(selected.upper())
        if numeric_level is None:
            if level is not None:
                raise ValueError("Unknown log level")
            numeric_level = logging.INFO
            invalid_environment_level = True
    else:
        numeric_level = selected

    shutdown_logging()
    root = logging.getLogger()
    root.setLevel(numeric_level)
    formatter = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = _ConsoleHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    path = _log_path()
    log_file: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        persistent = _LogFileHandler(
            path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
    except OSError as exc:
        # Report the type, never an arbitrary OS error containing private data.
        print(
            f"Could not open log file {path} ({type(exc).__name__}); logging to stderr.",
            file=sys.stderr,
        )
    else:
        persistent.setFormatter(formatter)
        root.addHandler(persistent)
        log_file = path

    for name in ("urllib3", "charset_normalizer"):
        logging.getLogger(name).setLevel(logging.WARNING)
    if invalid_environment_level:
        root.warning("Invalid INSIDER_SCANNER_LOG_LEVEL; using INFO")
    return log_file


def get_log_file() -> Path | None:
    """Return the active file destination, including a failed-setup fallback."""
    for handler in logging.getLogger().handlers:
        if isinstance(handler, _LogFileHandler):
            return Path(handler.baseFilename)
    return None


def diagnostic_hint() -> str:
    path = get_log_file()
    return f"See logs: {path}." if path is not None else "See stderr for details."


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"insider_scanner.{name}")
