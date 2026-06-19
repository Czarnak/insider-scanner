"""Single source of truth for the hardened lxml parser used on SEC XML.

SEC filing XML is untrusted input.  Every parse of such XML must disable
external-entity resolution and network access and avoid loading DTDs so a
malicious filing cannot trigger out-of-band fetches or entity-expansion
attacks.  Both ``sec_ownership_document`` and ``sec_ownership_parser`` build
their parser here so the hardening configuration can never drift between the
two parse sites.
"""

from __future__ import annotations

from lxml import etree


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
