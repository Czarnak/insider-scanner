"""Shared SEC ownership-form vocabulary for discovery and XML extraction."""

OWNERSHIP_FORM_TYPES: frozenset[str] = frozenset({"3", "3/A", "4", "4/A", "5", "5/A"})

# The scanner emits transactions, not initial positions. Form 3 only reports
# holdings, so discovering it would download a filing with no transaction rows.
# Keep the wider ownership set for explicit extraction/parsing of old documents.
TRANSACTION_FORM_TYPES: frozenset[str] = frozenset({"4", "4/A", "5", "5/A"})
