# Releasing insider-scanner

The release workflow first checks the tag against `pyproject.toml` and runs the
reusable CI workflow (offline tests with coverage, lint, formatting, types and
the locked dependency audit). It then builds and verifies one wheel and source distribution,
publishes those artifacts to TestPyPI, verifies the exact tagged version, and
then publishes the same artifacts to PyPI. TestPyPI verification first installs
the local candidate wheel with dependencies from PyPI, then replaces only the
application from TestPyPI using `--no-deps --force-reinstall`.

Publishing uses GitHub Trusted Publishing (OIDC). Do not create or store PyPI or
TestPyPI API tokens in GitHub secrets, repository files, or local release
configuration.

## One-time OIDC setup

### GitHub environments

In `Czarnak/insider-scan`, open **Settings -> Environments** and create:

- `testpypi`
- `pypi`

Add a required reviewer to `pypi` when production publication should require
manual approval.

### PyPI Trusted Publisher

On <https://pypi.org>, add a pending Trusted Publisher with these exact values:

- PyPI project name: `insider-scanner`
- GitHub owner: `Czarnak`
- GitHub repository: `insider-scan`
- Workflow filename: `release.yml`
- Environment name: `pypi`

### TestPyPI Trusted Publisher

On <https://test.pypi.org>, add the corresponding pending Trusted Publisher:

- TestPyPI project name: `insider-scanner`
- GitHub owner: `Czarnak`
- GitHub repository: `insider-scan`
- Workflow filename: `release.yml`
- Environment name: `testpypi`

No token or password is entered in the workflow. GitHub issues a short-lived
OIDC identity only to the publishing jobs, which require job-scoped
`id-token: write`; other workflow access remains read-only.

## Local release gate

Run the gate from a clean checkout before creating any release tag:

```bash
uv sync --locked --extra dev --extra gui --extra release
uv run --locked --extra dev --extra gui pytest -m "not live" --cov=insider_scanner --cov-report=term-missing --cov-fail-under=80
uv run --locked --extra dev --extra gui ruff check .
uv run --locked --extra dev --extra gui ruff format --check .
uv run --locked --extra dev --extra gui mypy src/insider_scanner
uv export --locked --extra dev --extra gui --no-emit-project --format requirements.txt --output-file audit-requirements.txt
uv run --locked --extra dev --extra gui pip-audit --requirement audit-requirements.txt --require-hashes --disable-pip --strict
uv run --locked --extra release python -m build
uv run --locked --extra release python -m twine check dist/*
```

Delete or move stale artifacts before `python -m build`; only artifacts created
from the release commit may be published. Review `git diff` and confirm that
runtime and distribution metadata both report the version in `pyproject.toml`.
Run the project's Procoder gate and test command as well.

Verify both installation modes from the newly built wheel in separate,
clean virtual environments:

```bash
python -m pip install dist/*.whl
insider-scanner-cli --help
insider-scanner-cli --version
python scripts/verify_install.py --mode base

wheel_uri="$(python -c "from pathlib import Path; print(next(Path('dist').glob('*.whl')).resolve().as_uri())")"
python -m pip install "insider-scanner[gui] @ ${wheel_uri}"
insider-scanner --verify-install
python scripts/verify_install.py --mode gui
```

## Pre-release guidance

A release-candidate tag such as `v2.0.0rc1` is still a real publication and must
match the package version (`2.0.0rc1` in that example). The
workflow may publish it to both TestPyPI and PyPI, where uploaded files and
versions cannot be replaced. Use a release candidate only when publishing a
public pre-release is intentional.

For a no-publication dry run, run the local release gate and verify the built
artifacts locally. Do not push a release tag.

## Cutting a release

1. Confirm the local release gate passes and the working tree contains only the
   intended release changes.
2. Set the intended version in `pyproject.toml`, refresh and commit `uv.lock`,
   and confirm `insider-scanner-cli --version` matches after reinstalling.
3. Commit the release changes using the repository's normal review process.
4. Read the committed version and create its matching tag:
   `version="$(python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])")"`
   followed by `git tag "v${version}"`.
5. Push the branch and tag only after explicit approval:
   `git push origin <branch>` followed by `git push origin "v${version}"`.
6. Watch the `release` workflow. TestPyPI publication and verification must
   complete before the `pypi` environment can publish.
7. Verify clean installations:
   `insider-scanner-cli` from the base package and `insider-scanner` from
   `insider-scanner[gui]`.
