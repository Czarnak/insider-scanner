"""Single source of truth for hardened SEC XML parsing and resource guards.

SEC filing XML is untrusted input.  Every parse of such XML must disable
external-entity resolution and network access and avoid loading DTDs so a
malicious filing cannot trigger out-of-band fetches or entity-expansion
attacks.  The extraction boundary builds its parser here; standalone documents use the
same validation path before transaction parsing.

Limit violations raise a reason-coded, payload-free :class:`SecXmlSecurityError`
(mirroring the SEC client/downloader security errors).  Callers map a raw
``lxml`` ``XMLSyntaxError`` to their own malformed-input exception themselves, so
this module deliberately does not parse on the caller's behalf.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lxml import etree

from insider_scanner.core.sec_security import SecSecurityPolicy, SecSecurityReason

if TYPE_CHECKING:
    from lxml.etree import _Element


# A DTD or entity declaration in untrusted SEC XML is always rejected, in
# addition to lxml's disabled DTD/entity resolution (defense in depth).


# Byte-order marks for non-UTF-8 encodings.  The declaration scan below is a
# UTF-8 byte pattern, so UTF-16/UTF-32 input would slip past it; we reject those
# encodings outright. ASCII-compatible declared encodings remain supported.  The two-byte UTF-16
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
    if xml_bytes.startswith(_NON_UTF8_BOMS) or b"\x00" in xml_bytes:
        raise SecXmlSecurityError(SecSecurityReason.XML, accession)
    cursor = 0
    while (start := xml_bytes.find(b"<", cursor)) != -1:
        # Consume each literal once. Regex searches for a closing delimiter can
        # retry at every unterminated opener and become quadratic on bad input.
        terminator = None
        if xml_bytes.startswith(b"<!--", start):
            terminator = b"-->"
        elif xml_bytes.startswith(b"<![CDATA[", start):
            terminator = b"]]>"
        elif xml_bytes.startswith(b"<?", start):
            terminator = b"?>"
        if terminator is not None:
            end = xml_bytes.find(terminator, start + 2)
            if end == -1:
                raise SecXmlSecurityError(SecSecurityReason.XML, accession)
            cursor = end + len(terminator)
        else:
            if (
                xml_bytes[start : start + 9]
                .upper()
                .startswith((b"<!DOCTYPE", b"<!ENTITY"))
            ):
                raise SecXmlSecurityError(SecSecurityReason.XML, accession)
            cursor = start + 1


def guard_xml_tree(
    root: _Element,
    policy: SecSecurityPolicy,
    accession: str | None = None,
) -> None:
    """Enforce element-count, depth, and total-text limits in one DFS pass.

    Runs before any expensive field conversion and exits on the first breach.
    """
    if root.getroottree().docinfo.internalDTD is not None:
        raise SecXmlSecurityError(SecSecurityReason.XML, accession)
    element_count = 0
    text_chars = 0
    depth = 0
    for event, element in etree.iterwalk(
        root, events=("start", "end", "comment", "pi")
    ):
        if event == "end":
            depth -= 1
            continue
        node_depth = depth + 1
        if event == "start":
            depth = node_depth
        element_count += 1
        if element_count > policy.xml_max_elements or node_depth > policy.xml_max_depth:
            raise SecXmlSecurityError(SecSecurityReason.XML, accession)
        text_chars += len(element.text or "") + len(element.tail or "")
        text_chars += sum(len(value) for value in element.attrib.values())
        if text_chars > policy.xml_max_text_bytes:
            raise SecXmlSecurityError(SecSecurityReason.XML, accession)
    # UTF-8 requires at most four bytes per Unicode character. Most documents
    # are comfortably below the limit, so exact encodings are needed only near
    # the boundary. Include comments, PIs, tails and attributes in both passes.
    if text_chars * 4 > policy.xml_max_text_bytes:
        text_bytes = 0
        for element in root.iter():
            values = (element.text or "", element.tail or "", *element.attrib.values())
            text_bytes += sum(len(value.encode("utf-8")) for value in values)
            if text_bytes > policy.xml_max_text_bytes:
                raise SecXmlSecurityError(SecSecurityReason.XML, accession)
