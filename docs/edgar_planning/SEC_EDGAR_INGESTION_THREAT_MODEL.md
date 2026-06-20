# SEC EDGAR Ingestion Threat Model

## Overview

Insider Scanner is a local desktop and CLI application that downloads public
SEC EDGAR metadata and ownership filings, parses Form 3/4/5 documents, and will
later persist normalized transactions locally. This threat model covers the SEC
ingestion subsystem in `src/insider_scanner/core/edgar.py`, `sec_client.py`,
`sec_downloader.py`, `sec_ownership_document.py`,
`sec_ownership_parser.py`, `sec_index.py`, and `sec_bulk.py`.

The application runs with the current user's filesystem and network privileges.
It is not a multi-tenant service and currently exposes no server endpoint. The
primary security goals are to prevent untrusted SEC content from causing
unexpected network access, filesystem escape, resource exhaustion, executable
markup, sensitive diagnostic leakage, or corrupt downstream records.

## Threat Model, Trust Boundaries, and Assumptions

### Assets and privileges

- The current user's files and credentials accessible to the application.
- Integrity and availability of the local application cache and future database.
- Correct provenance and interpretation of ownership transactions.
- SEC access identity and operational logs, which must not collect raw payloads.
- Host memory, disk, CPU, and network availability during ingestion.

### Trust boundaries

1. **Application to network:** URLs and redirects cross from local trusted code
   to remote hosts. TLS validates transport identity, but every redirect target
   still requires application-level allowlisting.
2. **SEC response to parser:** Filing text, XML, index rows, response headers,
   content types, compressed archives, and JSON are untrusted data even when
   served by an approved SEC host. Filing submitters can influence document
   content, and upstream data can be malformed or unexpectedly large.
3. **Operator to bulk parser:** An operator-selected ZIP path is trusted only as
   a selection. Archive members and metadata remain untrusted and the selected
   file must remain inside the configured EDGAR cache root.
4. **Parser to filesystem:** Network bytes cannot choose local paths. Only
   parser-approved payloads may cross into the validated cache namespace.
5. **Core modules to diagnostics:** Exception and log boundaries may receive
   attacker-controlled parser context. Only validated identifiers and stable
   reason codes may cross this boundary.
6. **Parsed data to future UI/database:** Parser output is data, never markup or
   code. Persistence and rendering are outside Session 3 but depend on these
   invariants.

### Input ownership

- **Attacker-controlled:** response bodies and headers, redirects, XML/SGML
  envelopes, footnotes, numeric strings, archive member metadata, compressed
  JSON, and malformed filing metadata.
- **Operator-controlled:** SEC contact identity, application cache root, and the
  bulk archive explicitly selected for processing.
- **Developer-controlled:** immutable default limits, approved hostnames,
  resource profiles, schema mappings, and fixtures.

### Security invariants

- A request is sent only to an exact approved HTTPS SEC hostname.
- Redirect validation happens before the redirected request.
- Every network, XML, text, numeric, JSON, and ZIP operation is bounded.
- Remote data cannot influence a local absolute path or escape the trusted root.
- Unvalidated bytes do not persist on disk and failed temporary writes are
  removed.
- XML parsing cannot resolve external resources or expand declared entities.
- Archive parsing never extracts members to disk.
- Footnotes and remarks remain bounded plain text.
- Errors and logs never contain raw response bodies, XML fragments, footnotes,
  or unsanitized third-party exception messages.

## Attack Surface, Mitigations, and Attacker Stories

### HTTP and redirect handling

A malicious or compromised endpoint can redirect the client to an internal,
local, or attacker-controlled host. Following redirects automatically would
cross the network trust boundary before validation. The client therefore
disables automatic redirects, resolves each `Location`, and allows only exact
`www.sec.gov` and `data.sec.gov` HTTPS targets with bounded redirect count.
Responses are streamed with connect/read timeouts, media-type profiles, and
declared plus actual byte limits.

### Cache and path handling

Archive paths, accessions, CIKs, and member names can contain traversal,
absolute paths, drive prefixes, separators, control characters, or symlink
tricks. Remote identifiers are never used as local filenames. Cache names are
derived locally, destination containment is checked against the configured
EDGAR cache root, symlinks are rejected, and atomic temporary files are created
inside the destination directory. Only successfully parsed payloads are
promoted into a versioned validated cache.

### XML and ownership fields

An ownership document can attempt XXE, DTD/entity expansion, deep or broad-tree
resource exhaustion, oversized text, pathological numeric conversion, or markup
in footnotes. The parser rejects DTD/entity declarations, disables entity and
network resolution, keeps libxml2 huge-tree support disabled, and applies byte,
node, depth, aggregate-text, field, and numeric limits. Footnote element markup
is flattened and whitespace-normalized before entering immutable model objects.

### Bulk ZIP and JSON

A selected archive can use traversal names, Windows paths, symlink entries,
misleading size metadata, extreme member counts, or compression bombs. The
parser validates the entire central directory before reading a member, enforces
name/count/member/total/ratio limits, streams bounded JSON, and never invokes an
extraction API. A violation rejects the archive rather than skipping the
malicious member.

### Diagnostics

Parser libraries can include input fragments in exception text, and careless
logging can duplicate whole filings or hostile markup. Public errors use typed
reason codes and safe labels. Warning logs include only stage and validated
provenance. Original parser exception text remains chained for local debugging
but is not copied into public messages or logs.

### Out-of-scope attacker stories

- A local attacker who already has the user's account privileges can directly
  modify the cache or application files; defending the whole user account is an
  operating-system responsibility. The ingestion code still rejects symlinks
  and unsafe paths to avoid amplifying that access.
- Compromise of the Python interpreter, trusted dependencies, the OS trust
  store, or the application update channel is outside this subsystem.
- Database authorization, GUI sandboxing, retry policy, and background-job
  isolation belong to later sessions. Session 3 ensures their inputs are bounded
  and plain-text.

## Severity Calibration

### Critical

- Untrusted SEC or ZIP content achieves arbitrary code execution in the local
  process.
- Archive or cache handling permits arbitrary file overwrite outside the EDGAR
  cache with realistic impact on executable or credential files.

### High

- Redirect handling sends requests to arbitrary internal/local hosts before
  validation.
- XXE reads local files or reaches network resources.
- A small response or archive causes reliably unbounded memory, disk, or CPU
  consumption that makes the desktop application or workstation unavailable.
- Rejected payloads containing sensitive local context are persistently logged
  or written outside the configured cache.

### Medium

- A malformed filing bypasses size or numeric limits and causes a bounded crash
  of one ingestion run without broader workstation impact.
- Raw markup reaches a future rendering layer as data that could become XSS if
  another component renders it unsafely.
- Unsafe cache reuse causes stale or attacker-modified data to be treated as
  parser-approved without escaping the local application boundary.

### Low

- A malformed public filing produces a sanitized, accession-scoped parse error
  or omits one record without filesystem, network, or resource-boundary impact.
- Diagnostics disclose only already-public accession, CIK, form type, or an
  approved SEC URL path.
- Availability impact requires a malicious local operator repeatedly selecting
  known-invalid archives and remains confined to that invocation.
