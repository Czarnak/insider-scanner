# Releasing insider-scanner

The release workflow builds and verifies one wheel and source distribution,
publishes those artifacts to TestPyPI, verifies the exact tagged version, and
then publishes the same artifacts to PyPI.

Publishing uses GitHub Trusted Publishing (OIDC). Do not create or store PyPI or
TestPyPI API tokens in GitHub secrets, repository files, or local release
configuration.

## One-time OIDC setup

### GitHub environments

In `Czarnak/insider-scanner`, open **Settings -> Environments** and create:

- `testpypi`
- `pypi`

Add a required reviewer to `pypi` when production publication should require
manual approval.

### PyPI Trusted Publisher

On <https://pypi.org>, add a pending Trusted Publisher with these exact values:

- PyPI project name: `insider-scanner`
- GitHub owner: `Czarnak`
- GitHub repository: `insider-scanner`
- Workflow filename: `release.yml`
- Environment name: `pypi`

### TestPyPI Trusted Publisher

On <https://test.pypi.org>, add the corresponding pending Trusted Publisher:

- TestPyPI project name: `insider-scanner`
- GitHub owner: `Czarnak`
- GitHub repository: `insider-scanner`
- Workflow filename: `release.yml`
- Environment name: `testpypi`

No token or password is entered in the workflow. GitHub issues a short-lived
OIDC identity only to the publishing jobs, which require job-scoped
`id-token: write`; other workflow access remains read-only.

## Local release gate

Run the gate from a clean checkout before creating any release tag. For 1.0.0:

```bash
python -m pip install -e ".[dev,gui,release]"
python -m pip install pip-audit
python -m pytest -m "not live" --cov=insider_scanner --cov-report=term-missing --cov-fail-under=80
python -m ruff check src tests scripts
python -m pip_audit
python -m build
python -m twine check dist/*
python -m pytest tests/test_package_data.py tests/test_packaging.py tests/test_packaging_metadata.py tests/test_readme_commands.py tests/test_verify_install_script.py
python scripts/verify_install.py --mode base
python scripts/verify_install.py --mode gui
```

Delete or move stale artifacts before `python -m build`; only artifacts created
from the release commit may be published. Review `git diff` and confirm that
runtime and distribution metadata both report `1.0.0`.

Verify both installation modes from the newly built 1.0.0 wheel in separate,
clean virtual environments:

```bash
python -m pip install "dist/insider_scanner-1.0.0-py3-none-any.whl"
insider-scanner-cli --help

wheel_uri="$(python -c "from pathlib import Path; print(next(Path('dist').glob('*.whl')).resolve().as_uri())")"
python -m pip install "insider-scanner[gui] @ ${wheel_uri}"
insider-scanner --verify-install
```

## Pre-release guidance

A release-candidate tag such as `v0.7.0rc1` is still a real publication. The
workflow may publish it to both TestPyPI and PyPI, where uploaded files and
versions cannot be replaced. Use a release candidate only when publishing a
public pre-release is intentional.

For a no-publication dry run, run the local release gate and verify the built
artifacts locally. Do not push a release tag.

## Cutting 1.0.0

1. Confirm the local release gate passes and the working tree contains only the
   intended release changes.
2. Confirm the version is `1.0.0` everywhere exposed to users and tooling.
3. Commit the release changes using the repository's normal review process.
4. Create the tag locally: `git tag v0.7.0`.
5. Push the branch and tag only after explicit approval:
   `git push origin <branch>` followed by `git push origin v0.7.0`.
6. Watch the `release` workflow. TestPyPI publication and verification must
   complete before the `pypi` environment can publish.
7. Verify clean installations:
   `insider-scanner-cli` from the base package and `insider-scanner` from
   `insider-scanner[gui]`.
