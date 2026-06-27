"""Behavior tests for the shared hardened-XML guards and security error.

TDD: written FIRST. They fail until _sec_xml.py grows the guards and the
reason-coded SecXmlSecurityError.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from lxml import etree

from insider_scanner.core._sec_xml import (
    SecXmlSecurityError,
    guard_xml_pre_parse,
    guard_xml_tree,
    hardened_xml_parser,
)
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecSecurityReason,
)


def _policy(**overrides: object):
    return replace(DEFAULT_SEC_SECURITY_POLICY, **overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Pre-parse byte ceiling
# ---------------------------------------------------------------------------


def test_pre_parse_rejects_oversized_bytes() -> None:
    with pytest.raises(SecXmlSecurityError) as exc_info:
        guard_xml_pre_parse(b"<ownershipDocument/>", _policy(xml_max_bytes=8))
    assert exc_info.value.reason is SecSecurityReason.XML


def test_pre_parse_allows_within_byte_ceiling() -> None:
    guard_xml_pre_parse(b"<x/>", DEFAULT_SEC_SECURITY_POLICY)


# ---------------------------------------------------------------------------
# Pre-parse DTD / entity declaration rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>',
        b'<!DOCTYPE foo SYSTEM "http://evil.example/x.dtd"><x/>',
        b'<!ENTITY xxe SYSTEM "file:///etc/passwd">',
        b"<!doctype html><x/>",
    ],
)
def test_pre_parse_rejects_dtd_and_entity_declarations(payload: bytes) -> None:
    with pytest.raises(SecXmlSecurityError):
        guard_xml_pre_parse(payload, DEFAULT_SEC_SECURITY_POLICY)


def test_pre_parse_allows_xml_decl_and_comments() -> None:
    guard_xml_pre_parse(
        b'<?xml version="1.0"?><!-- harmless --><x/>', DEFAULT_SEC_SECURITY_POLICY
    )


# ---------------------------------------------------------------------------
# Tree limits (element count / depth / total text)
# ---------------------------------------------------------------------------


def test_tree_rejects_excess_elements() -> None:
    root = etree.fromstring(b"<r><a/><b/><c/></r>", hardened_xml_parser())
    with pytest.raises(SecXmlSecurityError):
        guard_xml_tree(root, _policy(xml_max_elements=3))


def test_tree_rejects_excess_depth() -> None:
    root = etree.fromstring(b"<a><b><c/></b></a>", hardened_xml_parser())
    with pytest.raises(SecXmlSecurityError):
        guard_xml_tree(root, _policy(xml_max_depth=2))


def test_tree_rejects_excess_total_text() -> None:
    root = etree.fromstring(b"<r>aaaaaaaaaaaaaaaa</r>", hardened_xml_parser())
    with pytest.raises(SecXmlSecurityError):
        guard_xml_tree(root, _policy(xml_max_text_bytes=8))


def test_tree_allows_within_limits() -> None:
    root = etree.fromstring(b"<a><b><c>hi</c></b></a>", hardened_xml_parser())
    guard_xml_tree(root, DEFAULT_SEC_SECURITY_POLICY)


# ---------------------------------------------------------------------------
# Exception contract: reason-coded, accession-aware, payload-free message
# ---------------------------------------------------------------------------


def test_security_error_is_reason_only_message() -> None:
    err = SecXmlSecurityError(accession="0000320193-26-000061")
    assert str(err) == "SEC XML rejected (xml)"
    assert err.reason is SecSecurityReason.XML
    assert err.accession == "0000320193-26-000061"


@pytest.mark.parametrize("bom", [b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff"])
def test_pre_parse_rejects_non_utf8_boms(bom: bytes) -> None:
    with pytest.raises(SecXmlSecurityError):
        guard_xml_pre_parse(bom + b"<x/>", DEFAULT_SEC_SECURITY_POLICY)


def test_tree_counts_attribute_bytes_toward_total_text() -> None:
    root = etree.fromstring(b'<r a="aaaaaaaaaaaaaaaa"/>', hardened_xml_parser())
    with pytest.raises(SecXmlSecurityError):
        guard_xml_tree(root, _policy(xml_max_text_bytes=8))
