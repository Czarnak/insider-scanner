"""Derived replacement links; original source observations remain untouched.

SEC amendments restate individual lines; unmatched original lines survive.
Only a uniquely identified original can anchor an amendment chain. Missing or
ambiguous evidence is retained. Senate amendments replace complete reports.
Cross-source matches never coalesce separate native lots or accessions.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Table, and_, delete, exists, insert, or_, select
from sqlalchemy.engine import Connection

from insider_scanner.persistence.schema import (
    congress_trades,
    sec_ingested_filings,
    trade_replacements,
    us_trades,
)


def visible_condition(
    table: Table, *, raw: bool = False, sources: Iterable[str] | None = None
):
    """Exclude superseded evidence and, for effective reads, duplicate scrapers."""
    market = "us" if table is us_trades else "congress"
    requested = {sources} if isinstance(sources, str) else set(sources or ())
    reasons = ["superseded"]
    # A scraper-only view can still show its own observation. The combined view
    # always prefers the native filing and does not count that observation twice.
    if not raw and (not requested or "sec_edgar" in requested):
        reasons.append("duplicate")
    return ~exists(
        select(1).where(
            trade_replacements.c.market == market,
            trade_replacements.c.canonical_key == table.c.canonical_key,
            trade_replacements.c.reason.in_(reasons),
        )
    )


def _normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


def _owners(row: Mapping[str, Any]) -> tuple[str, ...]:
    try:
        detail = json.loads(row["sec_detail_json"])
        owners = tuple(
            sorted(
                {
                    str(owner["cik"]).strip().lstrip("0")
                    for owner in detail["reporting_owners"]
                }
            )
        )
        if owners and all(owners):
            return owners
    except (TypeError, ValueError, KeyError, AttributeError):
        pass
    owner = row["cik_insider"].strip().lstrip("0")
    return (owner,) if owner else ()


def _senate_supersessions(rows: list[Mapping[str, Any]]) -> dict[str, tuple[str, str]]:
    groups: dict[tuple, dict[str, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        if (
            row["source"] != "senate"
            or not row["period_of_report"]
            or not row["report_title"]
        ):
            continue
        identity = (
            _normalized(row["official_name"]),
            row["chamber"],
            row["period_of_report"],
        )
        filing = row["doc_id"].strip()
        if not identity[0] or row["chamber"] != "Senate":
            continue
        if filing and row["filing_date"]:
            groups[identity][filing].append(row)
    links: dict[str, tuple[str, str]] = {}
    for filings in groups.values():
        originals = [
            key
            for key, lots in filings.items()
            if not any(row["is_amendment"] for row in lots)
        ]
        if len(originals) > 1:
            continue
        candidates = {
            key: lots
            for key, lots in filings.items()
            if all(row["is_amendment"] for row in lots)
            and all(row["filing_complete"] for row in lots)
        }
        if not candidates:
            continue
        winner = max(
            candidates, key=lambda key: (candidates[key][0]["filing_date"], key)
        )
        winning_date = candidates[winner][0]["filing_date"]
        if originals and filings[originals[0]][0]["filing_date"] > winning_date:
            continue
        replacement = candidates[winner][0]["canonical_key"]
        for key, lots in filings.items():
            if key == winner:
                continue
            # Senate UUIDs cannot establish same-day filing order.
            if lots[0]["filing_date"] >= winning_date:
                continue
            for row in lots:
                links[row["canonical_key"]] = (replacement, "superseded")
    return links


def _detail(row: Mapping[str, Any]) -> dict:
    try:
        value = json.loads(row["sec_detail_json"])
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _decimal_key(value: Any) -> str:
    try:
        number = Decimal(str(value))
        if number.is_finite():
            return str(number.normalize())
    except (ValueError, InvalidOperation):
        pass
    return str(value)


def _line_key(row: Mapping[str, Any]) -> tuple | None:
    detail = _detail(row)
    title = _normalized(str(detail.get("security_title") or ""))
    if not title or not row["trade_date"] or not row["transaction_code"]:
        return None
    key: tuple = (
        title,
        row["trade_date"],
        row["transaction_code"],
        row["is_derivative"],
        row["acquired_disposed"],
        row["direct_or_indirect"],
    )
    if row["is_derivative"]:
        key += (
            detail.get("exercise_date"),
            detail.get("expiration_date"),
            _decimal_key(detail.get("conversion_or_exercise_price")),
            _normalized(str(detail.get("underlying_security_title") or "")),
        )
    return key


def _financial_key(row: Mapping[str, Any]) -> tuple:
    return tuple(
        (row[name], row[f"{name}_missing"])
        for name in ("shares", "price", "value", "shares_owned_after")
    )


def _narrative(row: Mapping[str, Any]) -> str:
    detail = _detail(row)
    footnotes = detail.get("footnotes")
    texts = list(footnotes.values()) if isinstance(footnotes, dict) else []
    return _normalized(
        " ".join(str(value) for value in [*texts, detail.get("remarks", "")])
    )


_ADDITION = re.compile(r"\b(?:add(?:s|ed|ing|ition|itional)?|omitt?\w*|unreported)\b")
_CORRECTION = re.compile(r"\bcorrect(?:s|ed|ing|ion|ions)?\b")


def _corrects_financial_fields(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    text = _narrative(new)
    if not _CORRECTION.search(text) or _ADDITION.search(text):
        return False
    field_words = {
        "shares": r"\b(?:number of shares|share count|transaction shares|quantity)\b",
        "price": r"\bprice\b",
        "shares_owned_after": r"\b(?:holdings|owned|ownership|balance)\b",
    }
    changed = [
        name
        for name in field_words
        if (old[name], old[f"{name}_missing"]) != (new[name], new[f"{name}_missing"])
    ]
    if not changed:
        return bool(re.search(r"\b(?:value|amount|proceeds)\b", text))
    return all(re.search(field_words[name], text) for name in changed)


def _replace_sec_lines(
    active: list[Mapping[str, Any]],
    incoming: list[Mapping[str, Any]],
    links: dict[str, tuple[str, str]],
) -> list[Mapping[str, Any]]:
    available = {row["canonical_key"]: row for row in active}
    pending = []
    for row in incoming:
        key = _line_key(row)
        if key is None or _ADDITION.search(_narrative(row)):
            continue
        exact = [
            old
            for old in available.values()
            if _line_key(old) == key and _financial_key(old) == _financial_key(row)
        ]
        if exact:
            old = min(exact, key=lambda item: item["canonical_key"])
            links[old["canonical_key"]] = (row["canonical_key"], "superseded")
            del available[old["canonical_key"]]
        else:
            pending.append(row)
    # No ordinal guessing: amendment tables omit unchanged lines. A differing
    # line needs one candidate and narrative evidence about the changed field.
    for row in pending:
        candidates = [
            old for old in available.values() if _line_key(old) == _line_key(row)
        ]
        competing = [new for new in pending if _line_key(new) == _line_key(row)]
        if len(candidates) == len(competing) == 1 and _corrects_financial_fields(
            candidates[0], row
        ):
            old = candidates[0]
            links[old["canonical_key"]] = (row["canonical_key"], "superseded")
            del available[old["canonical_key"]]
    return [*available.values(), *incoming]


def _sec_supersessions(rows: list[Mapping[str, Any]]) -> dict[str, tuple[str, str]]:
    groups: dict[tuple, dict[str, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        issuer, owners = row["cik_issuer"].strip().lstrip("0"), _owners(row)
        form = row["form_type"].upper().removesuffix("/A")
        if not (
            row["accession_number"]
            and row["sec_row_id"]
            and issuer
            and owners
            and row["period_of_report"]
            and form
            and row["filing_date"]
        ):
            continue
        groups[(issuer, owners, row["period_of_report"], form)][
            row["accession_number"]
        ].append(row)
    links: dict[str, tuple[str, str]] = {}
    for filings in groups.values():
        originals = {
            key: lots
            for key, lots in filings.items()
            if not any(row["is_amendment"] for row in lots)
        }
        active = {key: list(lots) for key, lots in originals.items()}
        amendments = [
            lots
            for lots in filings.values()
            if all(
                row["is_amendment"] and row.get("_filing_status") in {None, "success"}
                for row in lots
            )
        ]
        for lots in sorted(
            amendments,
            key=lambda items: (items[0]["filing_date"], items[0]["accession_number"]),
        ):
            dates = {row["original_submission_date"] for row in lots}
            if len(dates) != 1:
                continue
            original_date = next(iter(dates))
            candidates = [
                key
                for key, original in originals.items()
                if original_date is None or original[0]["filing_date"] == original_date
            ]
            if len(candidates) != 1:
                continue
            original_key = candidates[0]
            if originals[original_key][0]["filing_date"] > lots[0]["filing_date"]:
                continue
            active[original_key] = _replace_sec_lines(active[original_key], lots, links)
    for key, (replacement, reason) in list(links.items()):
        while replacement in links:
            replacement = links[replacement][0]
        links[key] = (replacement, reason)
    return links


def _economic_key(row: Mapping[str, Any]) -> tuple | None:
    if (
        not row["trade_date"]
        or not row["filing_date"]
        or not row["insider_name"].strip()
    ):
        return None
    if row["is_derivative"] or row["trade_type"] not in {"Buy", "Sell", "Exercise"}:
        return None
    if row["transaction_code"] not in {"", "P", "S", "M"}:
        return None
    if any(
        row[field] for field in ("shares_missing", "price_missing", "value_missing")
    ):
        return None
    return (
        row["ticker"],
        _normalized(row["insider_name"]),
        row["trade_date"],
        row["filing_date"],
        row["trade_type"],
    )


def _security(row: Mapping[str, Any]) -> str:
    try:
        return _normalized(
            str(json.loads(row["sec_detail_json"]).get("security_title", ""))
        )
    except (ValueError, TypeError, AttributeError):
        return ""


def _matches(scraper: Mapping[str, Any], lots: list[Mapping[str, Any]]) -> bool:
    return math.isclose(
        abs(scraper["shares"]),
        sum(abs(row["shares"]) for row in lots),
        rel_tol=0,
        abs_tol=0.000001,
    ) and math.isclose(
        abs(scraper["value"]),
        sum(abs(row["value"]) for row in lots),
        rel_tol=0,
        abs_tol=1.0,
    )


def _us_links(rows: list[Mapping[str, Any]]) -> dict[str, tuple[str, str]]:
    links = _sec_supersessions(rows)
    native: dict[tuple, dict[tuple, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        key = _economic_key(row)
        if key and row["accession_number"] and row["sec_row_id"]:
            native[key][(row["accession_number"], _security(row))].append(row)
    for row in rows:
        if row["accession_number"] and row["sec_row_id"]:
            continue
        key = _economic_key(row)
        if key is None:
            continue
        matches = []
        for lots in native.get(key, {}).values():
            single = [lot for lot in lots if _matches(row, [lot])]
            if len(single) == 1:
                matches.append(single[0])
            elif not single and len(lots) > 1 and _matches(row, lots):
                matches.append(lots[0])
        active = [match for match in matches if match["canonical_key"] not in links]
        if len(active) == 1:
            links[row["canonical_key"]] = (active[0]["canonical_key"], "duplicate")
        elif not active:
            replacements = {links[match["canonical_key"]] for match in matches}
            if len(replacements) == 1:
                links[row["canonical_key"]] = replacements.pop()
    return links


def rebuild_replacements(
    connection: Connection,
    table: Table,
    *,
    tickers: set[str] | None = None,
    issuers: set[str] | None = None,
) -> None:
    """Reconcile derived links atomically with the enclosing upsert/migration.

    US writes reload affected ticker/issuer scopes once per upsert, rather than
    once per input row. Congress has few observations and is rebuilt together
    so a corrected official name cannot leave stale links behind.
    """
    if table is not us_trades and table is not congress_trades:
        return
    statement = select(table)
    if table is us_trades:
        statement = select(
            table, sec_ingested_filings.c.status.label("_filing_status")
        ).outerjoin(
            sec_ingested_filings,
            sec_ingested_filings.c.accession == table.c.accession_number,
        )
    if table is us_trades and tickers is not None:
        conditions = [table.c.ticker.in_(sorted(tickers))]
        if issuers:
            conditions.append(table.c.cik_issuer.in_(sorted(issuers)))
            issuer_rows = table.alias("issuer_rows")
            conditions.append(
                table.c.ticker.in_(
                    select(issuer_rows.c.ticker).where(
                        issuer_rows.c.cik_issuer.in_(sorted(issuers))
                    )
                )
            )
        statement = statement.where(or_(*conditions))
    rows: list[Mapping[str, Any]] = [
        dict(row) for row in connection.execute(statement).mappings()
    ]
    market = "us" if table is us_trades else "congress"
    keys = [row["canonical_key"] for row in rows]
    for start in range(0, len(keys), 300):
        connection.execute(
            delete(trade_replacements).where(
                and_(
                    trade_replacements.c.market == market,
                    trade_replacements.c.canonical_key.in_(keys[start : start + 300]),
                )
            )
        )
    links = _us_links(rows) if table is us_trades else _senate_supersessions(rows)
    if links:
        connection.execute(
            insert(trade_replacements),
            [
                {
                    "market": market,
                    "canonical_key": key,
                    "replacement_key": replacement,
                    "reason": reason,
                }
                for key, (replacement, reason) in links.items()
            ],
        )
