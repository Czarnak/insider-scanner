"""Behavior tests for SEC ownership document extraction (Form 3/4/5)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# 1. OwnershipDocument is frozen and slotted
# ---------------------------------------------------------------------------


def test_ownership_document_is_frozen_and_slotted() -> None:
    from insider_scanner.core.sec_ownership_document import OwnershipDocument

    doc = OwnershipDocument(
        accession_number="0000320193-26-000061",
        document_type="4",
        xml_text="<ownershipDocument/>",
    )

    assert doc.__slots__ == ("accession_number", "document_type", "xml_text")
    assert not hasattr(doc, "__dict__")
    with pytest.raises(FrozenInstanceError):
        doc.document_type = "5"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. Full submission: correct block selected, provenance captured
# ---------------------------------------------------------------------------


def test_extract_from_full_submission_selects_ownership_block_not_decoy() -> None:
    from insider_scanner.core.sec_ownership_document import extract_ownership_document

    content = (FIXTURE_DIR / "sec_form4_submission.txt").read_text(encoding="utf-8")

    result = extract_ownership_document(content)

    assert result.accession_number == "0000320193-26-000061"
    assert result.document_type == "4"
    assert "<ownershipDocument" in result.xml_text
    assert "Common Stock" in result.xml_text
    assert "POWER OF ATTORNEY" not in result.xml_text


# ---------------------------------------------------------------------------
# 3. Extracted xml_text parses correctly with hardened lxml parser
# ---------------------------------------------------------------------------


def test_extracted_submission_xml_text_is_well_formed_ownership_document() -> None:
    from lxml import etree

    from insider_scanner.core.sec_ownership_document import extract_ownership_document

    content = (FIXTURE_DIR / "sec_form4_submission.txt").read_text(encoding="utf-8")
    result = extract_ownership_document(content)

    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )
    root = etree.fromstring(result.xml_text.encode("utf-8"), parser)
    local_name = etree.QName(root.tag).localname
    assert local_name == "ownershipDocument"


# ---------------------------------------------------------------------------
# 4. Direct XML with explicit accession_number echoes it back
# ---------------------------------------------------------------------------


def test_extract_from_direct_xml_with_accession_number_param() -> None:
    from insider_scanner.core.sec_ownership_document import extract_ownership_document

    content = (FIXTURE_DIR / "sec_form4_primary.xml").read_text(encoding="utf-8")

    result = extract_ownership_document(content, accession_number="0000320193-26-000061")

    assert result.document_type == "4"
    assert result.accession_number == "0000320193-26-000061"


# ---------------------------------------------------------------------------
# 5. Direct XML without accession_number param → None
# ---------------------------------------------------------------------------


def test_extract_from_direct_xml_without_accession_number_param() -> None:
    from insider_scanner.core.sec_ownership_document import extract_ownership_document

    content = (FIXTURE_DIR / "sec_form4_primary.xml").read_text(encoding="utf-8")

    result = extract_ownership_document(content)

    assert result.accession_number is None


# ---------------------------------------------------------------------------
# 6. Amendment fixture yields document_type == "4/A"
# ---------------------------------------------------------------------------


def test_extract_amendment_document_type() -> None:
    from insider_scanner.core.sec_ownership_document import extract_ownership_document

    content = (FIXTURE_DIR / "sec_form4_amendment.xml").read_text(encoding="utf-8")

    result = extract_ownership_document(content)

    assert result.document_type == "4/A"


# ---------------------------------------------------------------------------
# 7. Full submission with only a non-ownership block → NonOwnershipDocumentError
# ---------------------------------------------------------------------------


def test_full_submission_with_only_non_ownership_block_raises() -> None:
    from insider_scanner.core.sec_ownership_document import NonOwnershipDocumentError, extract_ownership_document

    content = """\
<SEC-DOCUMENT>
<SEC-HEADER>
ACCESSION NUMBER:\t\t0001234567-26-000001
CONFORMED SUBMISSION TYPE:\tEX-24
</SEC-HEADER>
<DOCUMENT>
<TYPE>EX-24
<SEQUENCE>1
<FILENAME>poa.txt
<DESCRIPTION>POWER OF ATTORNEY
<TEXT>
This is not an ownership document.
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>
"""

    with pytest.raises(NonOwnershipDocumentError):
        extract_ownership_document(content)


# ---------------------------------------------------------------------------
# 8. Direct XML with non-ownership root → NonOwnershipDocumentError
# ---------------------------------------------------------------------------


def test_direct_xml_with_non_ownership_root_raises() -> None:
    from insider_scanner.core.sec_ownership_document import NonOwnershipDocumentError, extract_ownership_document

    content = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission>
  <schemaVersion>X0101</schemaVersion>
</edgarSubmission>
"""

    with pytest.raises(NonOwnershipDocumentError):
        extract_ownership_document(content)


# ---------------------------------------------------------------------------
# 9. Malformed XML inside an ownership document → SecOwnershipDocumentError,
#    raw broken body NOT in the exception message
# ---------------------------------------------------------------------------


def test_malformed_xml_raises_and_body_not_in_error_message() -> None:
    from insider_scanner.core.sec_ownership_document import SecOwnershipDocumentError, extract_ownership_document

    broken_xml = "<ownershipDocument><issuer></ownershipDocument>"
    content = f"""\
<SEC-DOCUMENT>
<SEC-HEADER>
ACCESSION NUMBER:\t\t0001234567-26-000002
</SEC-HEADER>
<DOCUMENT>
<TYPE>4
<SEQUENCE>1
<FILENAME>form4.xml
<DESCRIPTION>PRIMARY DOCUMENT
<TEXT>
<XML>
{broken_xml}
</XML>
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>
"""

    with pytest.raises(SecOwnershipDocumentError) as exc_info:
        extract_ownership_document(content)

    assert broken_xml not in str(exc_info.value)


# ---------------------------------------------------------------------------
# 10. Wrong content type → TypeError
# ---------------------------------------------------------------------------


def test_wrong_content_type_raises_type_error() -> None:
    from insider_scanner.core.sec_ownership_document import extract_ownership_document

    with pytest.raises(TypeError):
        extract_ownership_document(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 11. bytes content is accepted and decoded (UTF-8)
# ---------------------------------------------------------------------------


def test_bytes_content_is_accepted() -> None:
    from insider_scanner.core.sec_ownership_document import extract_ownership_document

    content = (FIXTURE_DIR / "sec_form4_primary.xml").read_bytes()

    result = extract_ownership_document(content)

    assert result.document_type == "4"


# ---------------------------------------------------------------------------
# 12. bytes with leading UTF-8 BOM are handled
# ---------------------------------------------------------------------------


def test_bytes_with_bom_are_decoded_correctly() -> None:
    from insider_scanner.core.sec_ownership_document import extract_ownership_document

    raw = (FIXTURE_DIR / "sec_form4_primary.xml").read_bytes()
    # Prepend BOM
    with_bom = b"\xef\xbb\xbf" + raw

    result = extract_ownership_document(with_bom)

    assert result.document_type == "4"


# ---------------------------------------------------------------------------
# 13. bytes that are not valid UTF-8 → SecOwnershipDocumentError (no raw bytes
#     in message)
# ---------------------------------------------------------------------------


def test_invalid_utf8_bytes_raise_and_raw_not_in_message() -> None:
    from insider_scanner.core.sec_ownership_document import SecOwnershipDocumentError, extract_ownership_document

    bad_bytes = b"\xff\xfe invalid utf-8 \x80\x81"

    with pytest.raises(SecOwnershipDocumentError) as exc_info:
        extract_ownership_document(bad_bytes)

    # The raw byte sequence must not appear in the error message
    assert b"\xff\xfe" not in str(exc_info.value).encode("latin-1", errors="replace")
