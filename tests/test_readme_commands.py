from pathlib import Path


README = Path(__file__).resolve().parents[1] / "README.md"
RELEASING = Path(__file__).resolve().parents[1] / "docs" / "RELEASING.md"


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
