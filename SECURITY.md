# Security Policy

## Reporting a Vulnerability

Do not disclose suspected vulnerabilities in a public issue, discussion, pull
request, log, fixture, or screenshot.

Use GitHub's private vulnerability reporting or a draft repository security
advisory for `Czarnak/insider-scanner`. Include:

- the affected commit or release;
- the smallest reproducible input or sequence of actions;
- expected and observed behavior;
- realistic impact and required attacker control; and
- any suggested mitigation.

Remove credentials, API keys, personal data, local filesystem paths, raw SEC
filings, and other unrelated sensitive content. If private reporting is not
available, open a public issue containing only a request for a private contact
channel and no vulnerability details.

Maintainers should acknowledge a complete report, reproduce it privately,
assess severity using the repository threat model, prepare tests and a fix, and
coordinate disclosure after supported users can update.

## Supported Versions

Security fixes target the latest released version and the current default
development branch. Older releases may require upgrading before a fix can be
applied. A security advisory will state any narrower affected-version range.

## Security Boundaries

Insider Scanner is a local CLI and desktop application. It runs with the current
user's network and filesystem privileges. External websites, SEC responses,
filing contents, redirects, XML, JSON, PDFs, ZIP metadata, and operator-selected
files are untrusted inputs.

The SEC ingestion threat model is maintained at
`docs/edgar_planning/SEC_EDGAR_INGESTION_THREAT_MODEL.md`. Contributors changing
an ingestion boundary must preserve these invariants:

- network requests use approved HTTPS hosts and validate redirects before the
  redirected request;
- downloads, decompression, parsing, tree depth, text, and numeric conversion
  are bounded;
- remote identifiers never choose local absolute paths or escape trusted roots;
- unvalidated payloads are not persisted;
- XML cannot resolve external resources or expand declared entities;
- archives are inspected without extracting untrusted members;
- markup remains data and is rendered/exported as plain text unless a reviewed
  sanitizer policy explicitly says otherwise; and
- public errors and logs never contain raw payloads, secrets, footnotes, local
  paths, or unsanitized dependency exception text.

## Contributor Requirements

Security-sensitive changes require test-driven development. Add a failing test
for the abuse case before changing production code, then verify the focused and
full suites. Prefer immutable objects, schema validation at trust boundaries,
typed sanitized errors, dependency injection for deterministic tests, and
locally generated filenames.

Before committing:

```powershell
.\.venv\Scripts\python.exe -m pytest -v -p no:cacheprovider --basetemp=build\pytest-tmp
.\.venv\Scripts\python.exe -m pytest --cov=insider_scanner --cov-report=term-missing --cov-fail-under=80
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src tests
.\.venv\Scripts\python.exe -m pip_audit
```

Review `git diff` for credentials, raw payloads, unsafe diagnostics, weakened
limits, path handling, redirect behavior, parser options, and archive extraction
before requesting review. Critical and high-severity findings must be resolved
before merge.

## Secrets and Test Data

Never commit live credentials, SEC contact identities tied to private accounts,
tokens, passwords, cookies, private filings, or user databases. Use environment
variables for required identities and synthetic, minimal fixtures for tests.
Rotate any secret that enters version control or a public diagnostic immediately
and review the repository for equivalent exposures.
