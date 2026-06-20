"""Single source of truth for hardened SEC XML parsing and resource guards.

SEC filing XML is untrusted input.  Every parse of such XML must disable
external-entity resolution and network access and avoid loading DTDs so a
malicious filing cannot trigger out-of-band fetches or entity-expansion
attacks.  Both ``sec_ownership_document`` and ``sec_ownership_parser`` build
their parser here and share the byte/declaration/tree guards so the hardening
configuration can never drift between the two parse sites.

Limit violations raise a reason-coded, payload-free :class:`SecXmlSecurityError`
(mirroring the SEC client/downloader security errors).  Callers map a raw
``lxml`` ``XMLSyntaxError`` to their own malformed-input exception themselves, so
this module deliberately does not parse on the caller's behalf.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from lxml import etree

from insider_scanner.core.sec_security import SecSecurityPolicy, SecSecurityReason

if TYPE_CHECKING:
    from lxml.etree import _Element  # noqa: PLC2701


# A DTD or entity declaration in untrusted SEC XML is always rejected, in
# addition to lxml's disabled DTD/entity resolution (defense in depth).
_DTD_OR_ENTITY_DECL = re.compile(rb"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)

# Byte-order marks for non-UTF-8 encodings.  The declaration scan below is a
# UTF-8 byte pattern, so UTF-16/UTF-32 input would slip past it; we reject those
# encodings outright (callers always hand us UTF-8 bytes).  The two-byte UTF-16
# LE mark also prefixes the UTF-32 LE mark, so it covers both.
_NON_UTF8_BOMS = (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")


class SecXmlSecurityError(Exception):
    """Raised when untrusted SEC XML violates the immutable security policy.

    The message carries only a stable reason code — never XML or field values.
    The validated accession (when known) is attached as an attribute for
    accession-scoped diagnostics without leaking payload into the message.
    """

    def __init__(
        self,
        reason: SecSecurityReason = SecSecurityReason.XML,
        accession: str | None = None,
    ) -> None:
        self.reason = reason
        self.accession = accession
        super().__init__(f"SEC XML rejected ({reason.value})")


def hardened_xml_parser() -> etree.XMLParser:
    """Return a fresh lxml parser hardened against XXE and entity expansion.

    A new parser is returned on every call: lxml parsers are stateful and must
    not be shared across threads, so callers parse with their own instance.
    """
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )


def guard_xml_pre_parse(
    xml_bytes: bytes,
    policy: SecSecurityPolicy,
    accession: str | None = None,
) -> None:
    """Reject oversized payloads and DTD/entity declarations before parsing."""
    if len(xml_bytes) > policy.xml_max_bytes:
        raise SecXmlSecurityError(SecSecurityReason.XML, accession)
    if xml_bytes.startswith(_NON_UTF8_BOMS):
        raise SecXmlSecurityError(SecSecurityReason.XML, accession)
    if _DTD_OR_ENTITY_DECL.search(xml_bytes) is not None:
        raise SecXmlSecurityError(SecSecurityReason.XML, accession)


def guard_xml_tree(
    root: _Element,
    policy: SecSecurityPolicy,
    accession: str | None = None,
) -> None:
    """Enforce element-count, depth, and total-text limits in one DFS pass.

    Runs before any expensive field conversion and exits on the first breach.
    """
    element_count = 0
    text_bytes = 0
    stack: list[tuple[_Element, int]] = [(root, 1)]
    while stack:
        element, depth = stack.pop()
        element_count += 1
        if element_count > policy.xml_max_elements:
            raise SecXmlSecurityError(SecSecurityReason.XML, accession)
        if depth > policy.xml_max_depth:
            raise SecXmlSecurityError(SecSecurityReason.XML, accession)
        if element.text:
            text_bytes += len(element.text.encode("utf-8"))
        if element.tail:
            text_bytes += len(element.tail.encode("utf-8"))
        for attr_value in element.attrib.values():
            text_bytes += len(
                attr_value
                if isinstance(attr_value, bytes)
                else attr_value.encode("utf-8")
            )
        if text_bytes > policy.xml_max_text_bytes:
            raise SecXmlSecurityError(SecSecurityReason.XML, accession)
        stack.extend((child, depth + 1) for child in element)
