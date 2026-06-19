"""Locate and validate the ownership XML from SEC Form 3/4/5 filings.

Supports two input shapes:
- Full submission text (.txt) with SEC-DOCUMENT / SEC-HEADER envelope
- Direct primary ownership XML document

Does NOT parse transactions.  Downstream modules handle that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lxml import etree

OWNERSHIP_FORM_TYPES: frozenset[str] = frozenset({"3", "3/A", "4", "4/A", "5", "5/A"})

# ---------------------------------------------------------------------------
# Value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OwnershipDocument:
    """Extracted ownership XML with filing provenance."""

    accession_number: str | None
    document_type: str | None
    xml_text: str


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class SecOwnershipDocumentError(Exception):
    """Base class for safe ownership-document extraction failures."""


class NonOwnershipDocumentError(SecOwnershipDocumentError):
    """Raised when no Form 3/4/5 ownership document can be located."""


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_ACCESSION_RE = re.compile(r"ACCESSION NUMBER:\s*([0-9\-]+)", re.IGNORECASE)
_DOCUMENT_BLOCK_RE = re.compile(
    r"<DOCUMENT>(.*?)</DOCUMENT>", re.DOTALL | re.IGNORECASE
)
_TYPE_LINE_RE = re.compile(r"<TYPE>([^\r\n]+)", re.IGNORECASE)
_TEXT_BLOCK_RE = re.compile(r"<TEXT>(.*?)</TEXT>", re.DOTALL | re.IGNORECASE)
_XML_WRAPPER_RE = re.compile(r"<XML>(.*?)</XML>", re.DOTALL | re.IGNORECASE)
_DOCUMENT_TYPE_ELEMENT_RE = re.compile(
    r"<documentType>\s*([^<]+?)\s*</documentType>", re.IGNORECASE
)

# Presence of any of these tags means it is a full submission envelope.
_FULL_SUBMISSION_MARKERS = ("<SEC-DOCUMENT", "<SEC-HEADER", "<DOCUMENT>")

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_ownership_document(
    content: bytes | str,
    *,
    accession_number: str | None = None,
) -> OwnershipDocument:
    """Extract the Form 3/4/5 ownership XML from an SEC filing payload.

    Parameters
    ----------
    content:
        Raw filing bytes (decoded as UTF-8) or already-decoded text.
    accession_number:
        Caller-supplied accession number; used as a fallback when the header
        cannot be parsed (full submission) or as the sole source (direct XML).

    Returns
    -------
    OwnershipDocument
        Immutable provenance record carrying the validated XML text.

    Raises
    ------
    TypeError
        ``content`` is neither ``bytes`` nor ``str``.
    SecOwnershipDocumentError
        Byte decoding fails, or the extracted XML is not well-formed.
    NonOwnershipDocumentError
        No Form 3/4/5 block can be located, or the XML root element is not
        ``ownershipDocument``.
    """
    if not isinstance(content, (bytes, str)):
        raise TypeError(
            f"content must be bytes or str, got {type(content).__name__!r}"
        )

    text = _decode(content)
    stripped = text.lstrip()

    if _is_full_submission(stripped):
        return _extract_from_full_submission(stripped, accession_number)

    if "<ownershipDocument" in stripped:
        return _extract_direct_xml(stripped, accession_number)

    raise NonOwnershipDocumentError(
        "Content does not appear to be an SEC submission or ownership XML document."
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _decode(content: bytes | str) -> str:
    """Return decoded text; raises SecOwnershipDocumentError on bad bytes."""
    if isinstance(content, str):
        return content
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SecOwnershipDocumentError(
            "Failed to decode filing content as UTF-8."
        ) from exc


def _is_full_submission(text: str) -> bool:
    return any(marker in text for marker in _FULL_SUBMISSION_MARKERS)


def _extract_from_full_submission(
    text: str, fallback_accession: str | None
) -> OwnershipDocument:
    """Parse a full SEC submission envelope and return the ownership block."""
    # Extract accession number from header (prefer header over parameter).
    accession_match = _ACCESSION_RE.search(text)
    accession_number = (
        accession_match.group(1).strip() if accession_match else fallback_accession
    )

    # Walk every <DOCUMENT>…</DOCUMENT> block in document order.
    for block_match in _DOCUMENT_BLOCK_RE.finditer(text):
        block_body = block_match.group(1)

        type_match = _TYPE_LINE_RE.search(block_body)
        if type_match is None:
            continue
        doc_type = type_match.group(1).strip()
        if doc_type not in OWNERSHIP_FORM_TYPES:
            continue

        # Found an ownership-typed block — extract its XML text.
        xml_text = _extract_xml_from_block(block_body)
        return _build_document(xml_text, accession_number, doc_type)

    raise NonOwnershipDocumentError(
        "No Form 3/4/5 <DOCUMENT> block found in SEC submission."
    )


def _extract_xml_from_block(block_body: str) -> str:
    """Pull the raw XML string from a <DOCUMENT> body."""
    text_match = _TEXT_BLOCK_RE.search(block_body)
    if text_match is None:
        return ""

    text_content = text_match.group(1)

    xml_match = _XML_WRAPPER_RE.search(text_content)
    if xml_match is not None:
        return xml_match.group(1).strip()

    return text_content.strip()


def _extract_direct_xml(text: str, accession_number: str | None) -> OwnershipDocument:
    """Handle a payload that is itself the primary ownership XML."""
    xml_text = text.strip()

    doc_type_match = _DOCUMENT_TYPE_ELEMENT_RE.search(xml_text)
    document_type = doc_type_match.group(1) if doc_type_match else None

    return _build_document(xml_text, accession_number, document_type)


def _build_document(
    xml_text: str,
    accession_number: str | None,
    document_type: str | None,
) -> OwnershipDocument:
    """Validate the XML and construct an immutable OwnershipDocument."""
    _validate_ownership_xml(xml_text, accession_number)
    return OwnershipDocument(
        accession_number=accession_number,
        document_type=document_type,
        xml_text=xml_text,
    )


def _validate_ownership_xml(xml_text: str, accession_number: str | None) -> None:
    """Parse xml_text with a hardened lxml parser; raise on failure."""
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )
    try:
        root = etree.fromstring(xml_text.encode("utf-8"), parser)
    except etree.XMLSyntaxError as exc:
        acc_hint = f" (accession={accession_number})" if accession_number else ""
        raise SecOwnershipDocumentError(
            f"Ownership XML is not well-formed{acc_hint}."
        ) from exc

    local_name = etree.QName(root.tag).localname
    if local_name != "ownershipDocument":
        raise NonOwnershipDocumentError(
            f"Root element is {local_name!r}, expected 'ownershipDocument'."
        )
