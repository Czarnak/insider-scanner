# Security rules

Repo-level security rules the Procoder harness reads and follows. Edit
freely — what is written here wins over the built-in defaults.

## Blocking lines

- A detected secret always blocks. Remove it and rotate the credential after confirming it is real.
- SAST findings at ERROR severity block; WARNING and INFO are judged.
- Dependency vulnerabilities at CVSS 7.0 or above block; below that they are reported and judged.

## Review guidance

Start where data enters: `procoder index entrypoints` lists the mains and
the exported surface; `procoder index callers` and `procoder index graph`
trace how input reaches a finding. A finding on a path reachable from an
entry point outranks one that is not.

## False positives

A finding that is genuinely not a secret (a pinned action SHA, a test
fixture) is silenced the tool's own way: append `gitleaks:allow` as a
comment on the exact line, or add its fingerprint to `.gitleaksignore`.
Every allow is a reviewed decision; record why it is safe.

## Never

- Never echo a secret value into a report, a commit message, or a chat.
- Never silence a scanner instead of fixing or judging its finding.
