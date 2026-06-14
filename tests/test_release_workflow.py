from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_release_workflow_builds_from_clean_output_directory() -> None:
    content = _read(RELEASE_WORKFLOW)

    assert "rm -rf build dist" in content
    assert "python -m build" in content
    assert "python -m twine check dist/*" in content


def test_release_workflow_verifies_base_gui_and_sdist_installations() -> None:
    content = _read(RELEASE_WORKFLOW)

    assert "python -m pip install dist/*.whl" in content
    assert "python scripts/verify_install.py --mode base" in content
    assert 'python -m pip install "insider-scanner[gui] @ ' in content
    assert "python scripts/verify_install.py --mode gui" in content
    assert "python -m pip install dist/*.tar.gz" in content
    assert "sdist_uri=" in content
    assert 'python -m pip install "insider-scanner[gui] @ ${sdist_uri}"' in content


def test_testpypi_verification_is_version_pinned_and_retried() -> None:
    content = _read(RELEASE_WORKFLOW)

    assert '"insider-scanner[gui]==${ver}"' in content
    assert "for attempt in 1 2 3 4 5 6;" in content
    assert "exit 1" in content


def test_workflows_pin_third_party_actions_to_commit_shas() -> None:
    action_pattern = re.compile(r"uses:\s+([^@\s]+)@([^\s#]+)")

    for workflow in (RELEASE_WORKFLOW, CI_WORKFLOW):
        for action, reference in action_pattern.findall(_read(workflow)):
            assert re.fullmatch(r"[0-9a-f]{40}", reference), (
                f"{workflow.name}: {action} is not pinned to a commit SHA"
            )


def test_ci_enforces_minimum_coverage() -> None:
    content = _read(CI_WORKFLOW)

    assert content.count("--cov-fail-under=80") == 2


def test_release_workflow_limits_oidc_to_publish_jobs() -> None:
    content = _read(RELEASE_WORKFLOW)

    assert "permissions:\n    contents: read" in content
    assert content.count("id-token: write") == 2

    publish_testpypi = content.split("    publish-testpypi:", maxsplit=1)[1].split(
        "    verify-testpypi:", maxsplit=1
    )[0]
    publish_pypi = content.split("    publish-pypi:", maxsplit=1)[1]
    assert "id-token: write" in publish_testpypi
    assert "id-token: write" in publish_pypi
