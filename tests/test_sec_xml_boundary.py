"""Regression coverage for the extraction-to-parser trust boundary."""

import hashlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from lxml import etree

from insider_scanner.core._sec_xml import SecXmlSecurityError
from insider_scanner.core.sec_ownership_document import (
    NonOwnershipDocumentError,
    OwnershipDocument,
    extract_ownership_document,
)
from insider_scanner.core.sec_ownership_parser import parse_ownership_document
from insider_scanner.core.sec_security import DEFAULT_SEC_SECURITY_POLICY

XML = (
    (Path(__file__).parent / "fixtures/sec_form4_primary.xml")
    .read_text(encoding="utf-8")
    .strip()
)


@pytest.mark.parametrize("encoding", ["ISO-8859-1", "windows-1252"])
@pytest.mark.parametrize("envelope", [False, True])
def test_source_encoding_and_unrelated_binary_attachment(encoding, envelope):
    text = XML.replace("UTF-8", encoding).replace("Apple Inc.", "Société Café")
    source = text.encode(encoding)
    payload = source
    if envelope:
        payload = (
            b"<SEC-DOCUMENT><DOCUMENT><TYPE>EX-24\n<TEXT>\xff\x80</TEXT></DOCUMENT>"
            b"<DOCUMENT><TYPE>4\n<TEXT><XML>"
            + source
            + b"</XML></TEXT></DOCUMENT></SEC-DOCUMENT>"
        )
    filing = parse_ownership_document(extract_ownership_document(payload))
    assert filing.issuer.name == "Société Café"
    assert filing.document_sha256 == hashlib.sha256(source).hexdigest()


def test_already_decoded_unicode_ignores_source_encoding_declaration():
    text = XML.replace("UTF-8", "ISO-8859-1").replace("Apple Inc.", "Société Café")
    for document in (
        extract_ownership_document(text),
        OwnershipDocument(None, "4", text),
    ):
        assert parse_ownership_document(document).issuer.name == "Société Café"


def test_extraction_and_parsing_use_one_hardened_parse():
    with patch("lxml.etree.fromstring", wraps=etree.fromstring) as parse:
        document = extract_ownership_document(XML)
        first = parse_ownership_document(document)
        assert parse_ownership_document(document) == first
    assert parse.call_count == 1


@pytest.mark.parametrize(
    "limits",
    [
        {"xml_max_bytes": 10},
        {"xml_max_elements": 2},
        {"xml_max_depth": 2},
        {"xml_max_text_bytes": 2},
        {"xml_max_scalar_chars": 2},
    ],
)
def test_changed_policy_revalidates_extracted_document(limits):
    document = extract_ownership_document(XML)
    with pytest.raises(SecXmlSecurityError):
        parse_ownership_document(
            document, policy=replace(DEFAULT_SEC_SECURITY_POLICY, **limits)
        )


def test_replace_drops_cached_validation():
    document = extract_ownership_document(XML)
    altered = replace(document, xml_text="<!DOCTYPE x><ownershipDocument/>")
    with pytest.raises(SecXmlSecurityError):
        parse_ownership_document(altered)


@pytest.mark.parametrize(
    "root",
    [
        '<ownershipDocument xmlns="urn:other"/>',
        '<o:ownershipDocument xmlns:o="urn:other"/>',
    ],
)
def test_namespaced_ownership_is_explicitly_rejected(root):
    with pytest.raises(NonOwnershipDocumentError):
        extract_ownership_document(root)


def test_form_vocabulary_is_shared():
    from insider_scanner.core.sec_bulk import OWNERSHIP_FORMS as bulk
    from insider_scanner.core.sec_forms import (
        OWNERSHIP_FORM_TYPES,
        TRANSACTION_FORM_TYPES,
    )
    from insider_scanner.core.sec_index import OWNERSHIP_FORMS as daily

    assert bulk is daily is TRANSACTION_FORM_TYPES
    assert TRANSACTION_FORM_TYPES < OWNERSHIP_FORM_TYPES
