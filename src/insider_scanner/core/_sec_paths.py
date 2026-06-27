"""Shared filesystem-path safety predicates for SEC ingestion boundaries.

These helpers are pure (no I/O side effects beyond path resolution) and never
raise on policy violations.  Callers compose them and raise their own typed,
reason-coded security errors so each boundary keeps its own exception contract.
"""

from __future__ import annotations

from pathlib import Path


def resolves_within(path: Path, root: Path) -> bool:
    """Return True iff *path* resolves inside *root* (no traversal escape).

    Both paths are resolved with ``strict=False`` so not-yet-created targets are
    checked lexically (``..`` segments are collapsed).  Symlink rejection is the
    caller's responsibility — this predicate only answers containment.
    """
    resolved_root = root.resolve(strict=False)
    resolved_path = path.resolve(strict=False)
    return resolved_path.is_relative_to(resolved_root)
