import re
from pathlib import Path


README = Path(__file__).resolve().parents[1] / "README.md"


def test_readme_uses_published_install_and_command_names():
    content = README.read_text(encoding="utf-8")

    assert "pipx install insider-scan" in content
    assert "pip install insider-scan" in content
    assert "insider-scan            # launch the desktop GUI" in content
    assert "insider-scan-cli --help # command-line interface" in content
    assert "insider-scanner-cli" not in content
    assert re.search(r"insider-scanner(?:$|[^-])", content, re.MULTILINE) is None
    assert (
        re.search(
            r"(?m)^insider-scan-cli latest --since \d{4}-\d{2}-\d{2}\s*$",
            content,
        )
        is None
    )
    assert (
        "insider-scan-cli latest --since 2025-06-01 --until 2025-06-30" in content
    )


def test_readme_has_release_badges():
    content = README.read_text(encoding="utf-8")

    assert (
        "[![PyPI](https://img.shields.io/pypi/v/insider-scan.svg)]"
        "(https://pypi.org/project/insider-scan/)"
    ) in content
    assert (
        "[![CI](https://github.com/Czarnak/insider-scan/actions/workflows/"
        "ci.yml/badge.svg)]"
        "(https://github.com/Czarnak/insider-scan/actions/workflows/ci.yml)"
    ) in content
    assert (
        "[![Python](https://img.shields.io/pypi/pyversions/insider-scan.svg)]"
        "(https://pypi.org/project/insider-scan/)"
    ) in content
