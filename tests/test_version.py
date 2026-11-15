"""Public version reporting follows installed distribution metadata."""

import importlib.metadata
import runpy
from pathlib import Path
from unittest.mock import Mock

import pytest

from insider_scanner import cli


def test_package_version_uses_distribution_metadata(monkeypatch):
    metadata = Mock(return_value="9.8.7")
    monkeypatch.setattr(importlib.metadata, "version", metadata)
    namespace = runpy.run_path(str(Path(cli.__file__).with_name("__init__.py")))
    assert namespace["__version__"] == "9.8.7"
    metadata.assert_called_once_with("insider-scanner")


def test_cli_version_exits_before_startup(monkeypatch, capsys):
    startup = Mock(side_effect=AssertionError("version reached startup"))
    monkeypatch.setattr(cli, "__version__", "9.8.7", raising=False)
    monkeypatch.setattr(cli, "ensure_dirs", startup)
    with pytest.raises(SystemExit) as error:
        cli.run(["--version"], service_factory=startup)
    assert error.value.code == 0
    assert capsys.readouterr().out == "insider-scanner-cli 9.8.7\n"
    startup.assert_not_called()
