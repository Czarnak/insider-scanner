"""Extract and validate SEC ownership XML without decoding other attachments."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

from insider_scanner.core._sec_xml import (
    guard_xml_pre_parse,
    guard_xml_tree,
    hardened_xml_parser,
)
from insider_scanner.core.sec_forms import OWNERSHIP_FORM_TYPES
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecResourceProfile,
    SecSecurityPolicy,
)


@dataclass(frozen=True, slots=True)
class _ValidatedXml:
    source: bytes
    root: etree._Element
    policy: SecSecurityPolicy


@dataclass(frozen=True, slots=True)
class OwnershipDocument:
    """Ownership XML with provenance and a private, non-constructor parse cache.

    ``xml_text`` is Unicode with a UTF-8 declaration. Extracted documents retain
    the selected source bytes privately for parsing and document hashing.
    ``dataclasses.replace`` drops validation, even for metadata edits.
    """

    accession_number: str | None
    document_type: str | None
    xml_text: str
    _validation: _ValidatedXml | None = field(
        default=None, init=False, repr=False, compare=False
    )


class SecOwnershipDocumentError(Exception):
    """Safe ownership-document extraction failure."""


class NonOwnershipDocumentError(SecOwnershipDocumentError):
    """No supported ownership XML document was found."""


class UnsupportedOwnershipDocumentError(NonOwnershipDocumentError):
    """An ownership block uses legacy text or HTML."""


_ACCESSION_RE = re.compile(rb"ACCESSION NUMBER:\s*([0-9-]+)", re.IGNORECASE)
_DOCUMENT_BLOCK_RE = re.compile(
    rb"<DOCUMENT>(.*?)</DOCUMENT>", re.DOTALL | re.IGNORECASE
)
_TYPE_LINE_RE = re.compile(rb"<TYPE>([^\r\n]+)", re.IGNORECASE)
_TEXT_BLOCK_RE = re.compile(rb"<TEXT>(.*?)</TEXT>", re.DOTALL | re.IGNORECASE)
_XML_WRAPPER_RE = re.compile(rb"<XML>(.*?)</XML>", re.DOTALL | re.IGNORECASE)
_XML_PREAMBLE_RE = re.compile(rb"\A(?:\s+|<!--.*?-->|<\?.*?\?>)*", re.DOTALL)
_OWNERSHIP_ROOT_START_RE = re.compile(rb"<(?:[^\s<>/:]+:)?ownershipDocument(?=[\s/>])")
_ENCODING_RE = re.compile(
    r"(\A\ufeff?\s*<\?xml\b[^?]*?\bencoding\s*=\s*)(['\"])[^'\"]+\2"
)
_FULL_SUBMISSION_MARKERS = (b"<SEC-DOCUMENT", b"<SEC-HEADER", b"<DOCUMENT>")


def _unicode_xml_bytes(text: str) -> bytes:
    """Already-decoded text must not be decoded again using its declaration."""
    return _ENCODING_RE.sub(r'\1"UTF-8"', text, count=1).encode("utf-8")


def extract_ownership_document(
    content: bytes | str,
    *,
    accession_number: str | None = None,
    policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY,
) -> OwnershipDocument:
    """Select ownership XML as bytes, honoring its declared source encoding.

    SEC envelopes use ASCII delimiters. Only the selected XML is decoded; binary
    or differently encoded attachments have no bearing on ownership parsing.
    """
    if not isinstance(content, (bytes, str)):
        raise TypeError("content must be bytes or str")
    max_input = policy.limits_for(SecResourceProfile.FILING_DOCUMENT).max_bytes
    if len(content) > max_input:
        raise SecOwnershipDocumentError("Filing payload exceeds the size limit.")
    is_unicode = isinstance(content, str)
    raw = content.encode("utf-8") if isinstance(content, str) else content
    if len(raw) > max_input:
        raise SecOwnershipDocumentError("Filing payload exceeds the size limit.")
    raw = raw.strip()
    if any(marker in raw for marker in _FULL_SUBMISSION_MARKERS):
        match = _ACCESSION_RE.search(raw)
        accession_number = match.group(1).decode("ascii") if match else accession_number
        for block in _DOCUMENT_BLOCK_RE.finditer(raw):
            body = block.group(1)
            type_match = _TYPE_LINE_RE.search(body)
            if type_match is None:
                continue
            doc_type = type_match.group(1).strip().decode("ascii", errors="replace")
            if doc_type not in OWNERSHIP_FORM_TYPES:
                continue
            text_match = _TEXT_BLOCK_RE.search(body)
            if text_match is None:
                raise SecOwnershipDocumentError("Ownership block has no TEXT section.")
            selected = text_match.group(1).strip()
            wrapper = _XML_WRAPPER_RE.search(selected)
            if wrapper is not None:
                selected = wrapper.group(1).strip()
            elif not _looks_like_ownership_xml(selected):
                raise UnsupportedOwnershipDocumentError(
                    "Ownership filing is not available as machine-readable XML."
                )
            return _build_document(
                selected, accession_number, doc_type, policy, is_unicode
            )
        raise NonOwnershipDocumentError("No Form 3/4/5 DOCUMENT block found.")
    if not _looks_like_ownership_xml(raw):
        raise NonOwnershipDocumentError("Content is not ownership XML.")
    return _build_document(raw, accession_number, None, policy, is_unicode)


def _looks_like_ownership_xml(raw: bytes) -> bool:
    candidate = raw.removeprefix(b"\xef\xbb\xbf").lstrip()
    if candidate.startswith(b"<?xml"):
        return True
    candidate = _XML_PREAMBLE_RE.sub(b"", candidate, count=1)
    return candidate.startswith((b"<!DOCTYPE", b"<!ENTITY")) or bool(
        _OWNERSHIP_ROOT_START_RE.match(candidate)
    )


def _parse_xml(
    source: bytes, accession: str | None, policy: SecSecurityPolicy
) -> etree._Element:
    guard_xml_pre_parse(source, policy, accession)
    try:
        root = etree.fromstring(source, hardened_xml_parser())
    except (etree.XMLSyntaxError, ValueError, LookupError) as exc:
        raise SecOwnershipDocumentError("Ownership XML is not well-formed.") from exc
    guard_xml_tree(root, policy, accession)
    # The SEC ownership schema has no namespace. Reject qualified roots instead
    # of returning empty filings from downstream unqualified field lookups.
    if root.tag != "ownershipDocument":
        raise NonOwnershipDocumentError(
            "Root element is not unqualified ownershipDocument."
        )
    return root


def _build_document(
    source: bytes,
    accession: str | None,
    doc_type: str | None,
    policy: SecSecurityPolicy,
    is_unicode: bool,
) -> OwnershipDocument:
    if is_unicode:
        source = _unicode_xml_bytes(source.decode("utf-8"))
    root = _parse_xml(source, accession, policy)
    try:
        text = source.decode(root.getroottree().docinfo.encoding or "utf-8")
    except (UnicodeError, LookupError) as exc:
        raise SecOwnershipDocumentError("Unsupported ownership XML encoding.") from exc
    text = _ENCODING_RE.sub(r'\1"UTF-8"', text, count=1)
    document = OwnershipDocument(
        accession, doc_type or root.findtext("documentType"), text
    )
    object.__setattr__(document, "_validation", _ValidatedXml(source, root, policy))
    return document


def _validated_ownership_xml(
    document: OwnershipDocument, policy: SecSecurityPolicy
) -> tuple[etree._Element, bytes]:
    """Internal handoff; callers must not mutate or expose the cached tree."""
    cached = document._validation
    if cached is not None:
        if cached.policy == policy:
            return cached.root, cached.source
        source = cached.source
    else:
        source = _unicode_xml_bytes(document.xml_text)
    return _parse_xml(source, document.accession_number, policy), source
