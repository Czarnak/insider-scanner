# Insider Scanner — Agent Instructions

## Project

- This is a Python 3.11+ project using a `src/insider_scanner` package, with a CLI, desktop GUI, local SQLite persistence, and tests in `tests/`.
- Keep `pyproject.toml` and the committed `uv.lock` consistent when changing dependencies.
- Preserve compatibility with existing local data and database state when changing persistence code.

## Data and credentials

- Treat API keys, local databases, downloads, exports, and caches as user data. Do not commit them or print credential values in logs or command output.
- `scripts/api_key.txt` and `stateCongess_apiKey.txt` are ignored local credential files. Use existing configuration code rather than reading or logging their contents.

## Validation

- Use the project’s existing pytest configuration and add or update focused tests for behavior changes.
- The offline test suite is `uv run --extra dev --extra gui pytest -m "not live"`; tests marked `live` contact external services.
- Before calling work complete, run the Procoder gate and test command. A check that could not run is not a pass.

## Procoder workflow

- Keep generated host rule files in sync with this `AGENTS.md`; edit this file as the canonical source and regenerate host copies with Procoder.
- Do not add AI attribution lines to commits or pull requests.
- If a deliberate shortcut is necessary, document its ceiling and revisit condition with a `debt:` comment.
