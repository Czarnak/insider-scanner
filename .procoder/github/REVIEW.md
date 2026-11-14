# Pre-PR review rubric

A fresh-context reviewer reads the full branch diff before the PR is opened.
Fix Critical and Important findings first. Findings name file and line, what
breaks, and the fix.

Check every hunk for:

- User-supplied strings reaching a path, command, or query without validation.
- Error paths that swallow failures or report success for incomplete work.
- State that is computed twice but must agree.
- Repeated work inside loops that belongs outside them.
- Temporary files with predictable names or overly broad permissions.
- New behavior missing from dispatch, usage text, canonical lists, docs, or tests.
- Parsers and scanners missing empty, malformed, or hostile input cases.
- Test fixtures containing real credentials or literal scanner-trigger content.
- Documentation that does not match the code.

End with a verdict line that counts findings by severity, or exactly
"Nothing found — open the PR."
