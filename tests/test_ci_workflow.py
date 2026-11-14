"""Guard the CI contracts that keep local and hosted checks comparable."""

from __future__ import annotations

import shlex
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _workflow() -> dict:
    return yaml.safe_load(
        (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )


def _commands(job: dict) -> list[list[str]]:
    return [
        shlex.split(line)
        for step in job["steps"]
        for line in step.get("run", "").splitlines()
        if line.strip()
    ]


def test_ci_runs_the_same_offline_gui_suite_on_every_supported_platform() -> None:
    job = _workflow()["jobs"]["test"]

    assert set(job["strategy"]["matrix"]["os"]) == {
        "ubuntu-latest",
        "windows-latest",
    }
    assert set(job["strategy"]["matrix"]["python-version"]) == {
        "3.11",
        "3.12",
        "3.13",
        "3.14",
    }
    assert job["env"]["QT_QPA_PLATFORM"] == "offscreen"
    test_steps = [step for step in job["steps"] if "pytest" in step.get("run", "")]
    assert len(test_steps) == 1
    assert "if" not in test_steps[0]
    command = shlex.split(test_steps[0]["run"])
    assert command[command.index("-m") + 1] == "not live"
    assert "--cov-fail-under=80" in command
    assert not any(arg.startswith("--ignore") for arg in command)
    assert not any("xvfb" in arg for cmd in _commands(job) for arg in cmd)


def test_ci_uses_locked_tools_and_bounds_every_job() -> None:
    workflow = _workflow()

    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        assert 0 < job["timeout-minutes"] <= 20
        commands = _commands(job)
        syncs = [cmd for cmd in commands if cmd[:2] == ["uv", "sync"]]
        assert len(syncs) == 1
        for command in [syncs[0], *[cmd for cmd in commands if cmd[0] == "uv"]]:
            assert "--locked" in command
            assert {
                command[i + 1] for i, arg in enumerate(command) if arg == "--extra"
            } == {
                "dev",
                "gui",
            }
        assert not any("pip" in cmd[:2] for cmd in commands)
        setup_uv = [
            step
            for step in job["steps"]
            if step.get("uses", "").startswith("astral-sh/setup-uv@")
        ]
        assert len(setup_uv) == 1
        assert setup_uv[0]["with"]["version"]


def test_ci_enforces_lint_format_and_types() -> None:
    commands = _commands(_workflow()["jobs"]["quality"])

    assert any(cmd[-3:] == ["ruff", "check", "."] for cmd in commands)
    assert any(cmd[-4:] == ["ruff", "format", "--check", "."] for cmd in commands)
    assert any(cmd[-2:] == ["mypy", "src/insider_scanner"] for cmd in commands)


def test_ci_audits_the_exported_lock_without_resolving_again() -> None:
    commands = _commands(_workflow()["jobs"]["audit"])
    export = next(cmd for cmd in commands if cmd[:2] == ["uv", "export"])
    audit = next(cmd for cmd in commands if "pip-audit" in cmd)

    assert "--no-emit-project" in export
    assert export[export.index("--format") + 1] == "requirements.txt"
    assert (
        export[export.index("--output-file") + 1]
        == audit[audit.index("--requirement") + 1]
    )
    assert {"--require-hashes", "--disable-pip", "--strict"}.issubset(audit)
    assert "--ignore-vuln" not in audit


def test_dependabot_maintains_lockfile_and_pinned_actions() -> None:
    config = yaml.safe_load(
        (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")
    )

    assert config["version"] == 2
    assert {update["package-ecosystem"] for update in config["updates"]} == {
        "uv",
        "github-actions",
    }
    assert all(update["directory"] == "/" for update in config["updates"])
