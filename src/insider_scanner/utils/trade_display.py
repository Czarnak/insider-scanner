"""Formatting for reported trade numbers with explicit missing-value flags."""


def format_reported_number(
    value: float, *, missing: bool = False, decimals: int = 0, currency: bool = False
) -> str:
    """Show unavailable values as N/A while preserving reported zeros."""
    if missing:
        return "N/A"
    prefix = "$" if currency else ""
    return f"{prefix}{value:,.{decimals}f}"
