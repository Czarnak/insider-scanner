from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

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


def test_release_workflow_limits_oidc_to_publish_jobs() -> None:
    workflow = yaml.safe_load(_read(RELEASE_WORKFLOW))
    assert workflow["permissions"] == {"contents": "read"}
    oidc_jobs = {
        name
        for name, job in workflow["jobs"].items()
        if job.get("permissions", {}).get("id-token") == "write"
    }
    assert oidc_jobs == {"publish-testpypi", "publish-pypi"}


def test_release_artifacts_require_matching_version_and_ci() -> None:
    workflow = yaml.safe_load(_read(RELEASE_WORKFLOW))
    jobs = workflow["jobs"]
    assert jobs["quality"]["needs"] == "validate-version"
    assert jobs["quality"]["uses"] == "./.github/workflows/ci.yml"
    assert jobs["build"]["needs"] == "quality"
    ci = yaml.safe_load(_read(CI_WORKFLOW))
    # PyYAML's YAML 1.1 loader interprets the Actions key "on" as True.
    assert "workflow_call" in ci[True]


@pytest.mark.parametrize("tag, expected_code", [("v9.8.7", 0), ("v9.8.6", 1)])
def test_release_tag_validation_runs_before_build(tmp_path, tag, expected_code) -> None:
    workflow = yaml.safe_load(_read(RELEASE_WORKFLOW))
    step = next(
        step for step in workflow["jobs"]["validate-version"]["steps"] if "run" in step
    )
    script = step["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "9.8.7"\n', encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "RELEASE_TAG": tag},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == expected_code
    if expected_code:
        assert "does not match package version v9.8.7" in result.stderr
    else:
        assert "Release version verified: 9.8.7" in result.stdout


def test_testpypi_resolves_dependencies_only_from_pypi() -> None:
    workflow = yaml.safe_load(_read(RELEASE_WORKFLOW))
    commands = [
        step.get("run", "") for step in workflow["jobs"]["verify-testpypi"]["steps"]
    ]
    dependencies = next(cmd for cmd in commands if "wheel_uri=" in cmd)
    candidate = next(cmd for cmd in commands if "test.pypi.org/simple/" in cmd)
    assert "--index-url https://pypi.org/simple/" in dependencies
    assert commands.index(dependencies) < commands.index(candidate)
    assert "--no-deps --force-reinstall" in candidate
    assert "--extra-index-url" not in candidate
