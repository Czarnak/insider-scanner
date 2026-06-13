import re
from pathlib import Path


README = Path(__file__).resolve().parents[1] / "README.md"


def test_readme_uses_published_install_and_command_names():
    content = README.read_text(encoding="utf-8")

    assert "pipx install insider-scanner" in content
    assert "pip install insider-scanner" in content
    assert "insider-scanner            # launch the desktop GUI" in content
    assert "insider-scanner-cli --help # command-line interface" in content
    assert "insider-scanner-cli" not in content
    assert re.search(r"insider-scanner(?:$|[^-])", content, re.MULTILINE) is None
    assert (
        re.search(
            r"(?m)^insider-scanner-cli latest --since \d{4}-\d{2}-\d{2}\s*$",
            content,
        )
        is None
    )
    assert "insider-scanner-cli latest --since 2025-06-01 --until 2025-06-30" in content


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
