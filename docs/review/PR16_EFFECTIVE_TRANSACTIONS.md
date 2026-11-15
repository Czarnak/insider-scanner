# PR 16: effective transactions and amendment evidence

This implements `SECPARSE-02`, `USSVC-15`, `CONGRESS-M2`, `PERSIST-04` and
`USSVC-09` from [the PR grouping](PR_GROUPS_2026-11-14.md).

## Stored observations and effective reads

Raw transaction keys, source provenance, row IDs and timestamps remain intact.
`trade_replacements` records a market, original canonical key, replacement
canonical key and a `superseded` or `duplicate` reason. These links are derived
inside the same transaction as the source writes and SEC completion ledger.
Reparses rebuild affected ticker/issuer scopes, including prior ticker values.

US and Congress repository queries, latest US reads and the unified feed apply
replacement links before date/value filtering, counts and pagination. Services,
investigation views and alerts using those repositories inherit that behavior.
`UsTradeRepository.query_raw()` and `CongressTradeRepository.query_raw()` expose
retained observations. US raw reads can exclude superseded rows while retaining
independent SEC/scraper evidence through `include_superseded=False`.

Combined US reads prefer a matching native SEC transaction. A scraper-only source
selection still shows its own active observation. Native lots are never merged.
Cross-source inference requires matching ticker, normalized insider, transaction
and filing dates, trade kind, available quantity and value. Quantity tolerance is
0.000001 shares and value tolerance is one dollar for whole-dollar scraper
rounding. A scraper total may match several rows within one accession/security;
separate accessions and different securities are not combined.

## SEC amendments

[Form 4 General Instruction 9](https://www.sec.gov/files/form4.pdf) distinguishes
added transaction lines, corrected transaction lines and other amendments.
Unchanged lines should be omitted from an amendment. Consequently, replacing
an entire original filing with the latest Form 4/A is unsafe.

The implementation groups filings by normalized issuer CIK, the complete known
reporting-owner CIK set, report period and base form. An amendment's validated
`original_submission_date` must select a unique original accession. When that
date is absent, only a single original in the group can anchor the chain.
Filings are processed in filing-date/accession order regardless of ingestion
order. An SEC amendment marked incomplete by the ingestion ledger cannot replace
the original.

Transaction lines match on security title, date, code, derivative status and
ownership direction. Derivative contract terms also participate. Exact financial
restatements replace observations one for one, preserving repeated-lot counts.
Differing financial values require one remaining candidate and retained narrative
evidence of a correction concerning the changed financial field. Addition or
omission wording vetoes that inference. Unmatched original lines and added lines
remain effective. Chains resolve to their final replacement.

Truncated remarks, capped/missing referenced footnotes and truncated footnote
lists cannot authorize a line replacement: the omitted text could describe an
addition. A stale scraper aggregate is superseded when any constituent SEC line
is corrected, independent of raw insertion order.

This is conservative evidence-based inference, not a regulator-supplied line ID.
Unclear purpose, changed identity fields, missing metadata and ambiguous multiple
originals remain visible. These cases can still need manual comparison. The legacy
JSON `amends` display hint is retained for compatibility and is not authoritative.

## Senate amendments

Senate discovery preserves the report title, explicit `for MM/DD/YYYY` report
period, amendment marker and per-report parsing completeness. A complete later
amendment replaces a uniquely identified earlier report for the same official,
chamber and explicit period. Same-day UUIDs do not establish chronological order.
Partial reports and ambiguous/missing periods remain visible. Repeated lots within
the selected report remain separate.

Reports containing no transaction rows cannot currently carry filing metadata
through the trade repository. They do not suppress earlier reports. Supporting
that case requires a filing-level metadata transport and persistence contract.

## Migration and comparison

Migration 11 adds typed original-submission dates, Senate metadata and replacement
links. It rebuilds links from retained evidence without deleting raw observations
or changing their keys/timestamps. Historical Senate coverage is invalidated once
so a refresh can recover titles that older versions discarded. Missing SEC
narrative/header evidence can be recovered by a source reparse; it is not invented.

The SEC comparator reads active raw source observations independently. It compares
non-derivative Buy/Sell/Exercise transactions with compatible P/S/M codes, tolerates
whole-dollar rounding and supports weighted scraper totals within one filing and
security. Missing amounts remain different from reported zero. Exact matching
prefers rows that preserve remaining aggregate alternatives; unmatched reports
retain original accession/row evidence. Arbitrary subsets of a filing are not
searched for aggregate matches.
