"""Quarterly SEC imports using the shared filing mapper and atomic ledger."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from time import monotonic

from insider_scanner.core.sec_dataset import (
    DATASET_SECURITY_POLICY,
    iter_dataset_filings,
)
from insider_scanner.core.sec_ingestion import SecFilingOutcome, SecFilingStatus
from insider_scanner.core.sec_security import SecSecurityPolicy
from insider_scanner.core.sec_trade_mapping import (
    SecTradeMappingError,
    ownership_filing_to_trades,
)
from insider_scanner.services.common import not_cancelled
from insider_scanner.services.context import PersistenceContext
from insider_scanner.services.sec_persistence import SecTradeBatch
from insider_scanner.utils.logging import get_logger

_log = get_logger("sec_dataset")


@dataclass(frozen=True, slots=True)
class SecDatasetSummary:
    """This invocation's selected filings and committed transaction counts."""

    filings_discovered: int
    filings_parsed: int
    filings_committed: int
    transactions_inserted: int
    transactions_updated: int
    transactions_skipped: int
    skipped_resume: int
    failures: int
    rows_staged: int
    cancelled: bool
    elapsed_seconds: float
    outcomes: tuple[SecFilingOutcome, ...] = ()


class SecDatasetService:
    """Import a local quarter without making any per-filing network requests.

    No calendar coverage is inferred from a dataset: publication may move late
    quarter-end filings into the following archive. Successful accession ledger
    entries survive interruption; excluded or malformed filings never gain one.
    """

    def __init__(
        self,
        persistence: PersistenceContext,
        *,
        policy: SecSecurityPolicy = DATASET_SECURITY_POLICY,
        batch_filings: int = 200,
        batch_rows: int = 5_000,
    ) -> None:
        if batch_filings < 1 or batch_rows < 1:
            raise ValueError("SEC batch limits must be positive")
        self._persistence = persistence
        self._policy = policy
        self._batch_filings = batch_filings
        self._batch_rows = batch_rows

    def run(
        self,
        zip_path: Path,
        *,
        since: date | None = None,
        until: date | None = None,
        ciks: frozenset[str] | None = None,
        reimport: bool = False,
        cancelled: Callable[[], bool] = not_cancelled,
        on_progress: Callable[[SecDatasetSummary], None] | None = None,
    ) -> SecDatasetSummary:
        """Stage bounded TSVs, then publish selected complete filings in batches."""
        ledger = self._persistence.sec_ingestion
        batch = SecTradeBatch(
            ledger.commit,
            max_filings=self._batch_filings,
            max_rows=self._batch_rows,
        )
        started = monotonic()
        discovered = parsed = committed = skipped = rows_staged = 0
        stopped = False
        outcomes: list[SecFilingOutcome] = []
        last_report = started
        last_units = 0

        def cancellation_requested() -> bool:
            nonlocal stopped
            stopped = stopped or cancelled()
            return stopped

        def snapshot(*, final: bool = False) -> SecDatasetSummary:
            return SecDatasetSummary(
                discovered,
                parsed,
                committed,
                batch.upserts.inserted,
                batch.upserts.updated,
                batch.upserts.skipped,
                skipped,
                len(outcomes),
                rows_staged,
                stopped,
                max(0.0, monotonic() - started),
                tuple(outcomes) if final else (),
            )

        def report(*, force: bool = False) -> None:
            nonlocal last_report, last_units
            now = monotonic()
            units = rows_staged + discovered
            if on_progress is not None and (
                force or units - last_units >= 100 or now - last_report >= 2
            ):
                on_progress(snapshot())
                last_report, last_units = now, units

        def staging(count: int) -> None:
            nonlocal rows_staged
            rows_staged = count
            report()

        def drain(*, flush: bool = False, emit: bool = True) -> None:
            nonlocal committed
            results = batch.take_outcomes(flush=flush)
            for result in results:
                if result.status is SecFilingStatus.SUCCESS:
                    committed += 1
                else:
                    outcomes.append(result)
                    _log.warning(
                        "SEC dataset filing failed accession=%s stage=%s reason=%s",
                        result.accession,
                        result.stage,
                        result.reason,
                    )
            if results and emit:
                report(force=True)

        filings = iter_dataset_filings(
            zip_path,
            since=since,
            until=until,
            ciks=ciks,
            policy=self._policy,
            cancelled=cancellation_requested,
            on_progress=staging,
        )
        try:
            for item in filings:
                if cancellation_requested():
                    break
                discovered += 1
                record = ledger.get(item.accession)
                if (
                    record is not None
                    and record.status is SecFilingStatus.SUCCESS
                    and (not reimport or record.stage != "dataset")
                ):
                    skipped += 1
                    report()
                    continue
                if item.error or item.filing is None or item.index_row is None:
                    batch.add_outcome(
                        SecFilingOutcome(
                            item.accession,
                            SecFilingStatus.RETRYABLE_FAILURE,
                            "dataset_parse",
                            item.error or "Incomplete dataset filing",
                        )
                    )
                else:
                    try:
                        trades = ownership_filing_to_trades(item.filing, item.index_row)
                        mapped = []
                        for trade in trades:
                            detail = json.loads(trade.sec_detail_json)
                            detail.update(
                                dataset_representation=True, narrative_complete=False
                            )
                            # The TSV has no verified master-index archive path
                            # or XML bytes. Keep those unavailable, not invented.
                            mapped.append(
                                replace(
                                    trade,
                                    edgar_url="",
                                    sec_detail_json=json.dumps(
                                        detail,
                                        sort_keys=True,
                                        separators=(",", ":"),
                                    ),
                                )
                            )
                    except SecTradeMappingError as error:
                        batch.add_outcome(
                            SecFilingOutcome(
                                item.accession,
                                SecFilingStatus.RETRYABLE_FAILURE,
                                "dataset_mapping",
                                type(error).__name__,
                            )
                        )
                    else:
                        parsed += 1
                        batch.add(item.accession, mapped, representation="dataset")
                drain()
                report()
        except BaseException:
            try:
                drain(flush=True, emit=False)
            except BaseException as error:  # noqa: BLE001 - preserve original abort
                _log.warning(
                    "SEC dataset abort flush failed reason=%s", type(error).__name__
                )
            raise
        finally:
            filings.close()
        drain(flush=True, emit=False)
        cancellation_requested()
        summary = snapshot(final=True)
        if on_progress is not None:
            on_progress(summary)
        return summary
