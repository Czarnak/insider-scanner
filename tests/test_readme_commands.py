import argparse
import re
import shlex
from pathlib import Path

from insider_scanner.cli import build_parser

README = Path(__file__).resolve().parents[1] / "README.md"
RELEASING = Path(__file__).resolve().parents[1] / "docs" / "RELEASING.md"


def test_readme_documents_every_cli_command_and_alias():
    content = README.read_text(encoding="utf-8")
    parser = build_parser()
    commands = next(
        action.choices
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )

    missing = [
        name for name in commands if f"insider-scanner-cli {name}" not in content
    ]
    assert missing == [], f"CLI commands missing from README: {missing}"


def test_readme_cli_examples_parse_without_running_commands():
    content = README.read_text(encoding="utf-8")
    parser = build_parser()

    for example in re.findall(r"^insider-scanner-cli (.+)$", content, re.MULTILINE):
        argv = shlex.split(example, comments=True)
        if argv in (["--help"], ["--version"]):
            continue
        args = parser.parse_args(argv)
        assert callable(args.func), example


def test_readme_documents_base_cli_install():
    content = README.read_text(encoding="utf-8")

    assert "pip install insider-scanner" in content
    assert "insider-scanner-cli --help" in content


def test_readme_documents_gui_extra_install():
    content = README.read_text(encoding="utf-8")

    assert 'pip install "insider-scanner[gui]"' in content
    assert "insider-scanner            # desktop GUI" in content


def test_release_guide_documents_both_install_verification_modes():
    content = RELEASING.read_text(encoding="utf-8")

    assert "python scripts/verify_install.py --mode base" in content
    assert "python scripts/verify_install.py --mode gui" in content
    assert "insider-scanner --verify-install" in content
    assert ".whl[gui]" not in content


def test_readme_has_release_badges():
    content = README.read_text(encoding="utf-8")

    assert (
        "[![PyPI](https://img.shields.io/pypi/v/insider-scanner.svg)]"
        "(https://pypi.org/project/insider-scanner/)"
    ) in content
    assert (
        "[![CI](https://github.com/Czarnak/insider-scanner/actions/workflows/"
        "ci.yml/badge.svg)]"
        "(https://github.com/Czarnak/insider-scanner/actions/workflows/ci.yml)"
    ) in content
    assert (
        "[![Python](https://img.shields.io/pypi/pyversions/insider-scanner.svg)]"
        "(https://pypi.org/project/insider-scanner/)"
    ) in content
