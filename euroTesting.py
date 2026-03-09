#!/usr/bin/env python3
"""
Round 14 — Full RSS item dump + check if hash is in any RSS namespace.

We know:
  - PDF URL works: /back/api/v1/documents/2026/{doc_id}/{SHA256}.pdf
  - RSS gives doc IDs but does it also give us the hash?

Usage:  python check_eu_sources_v14.py
"""

import re
import requests
from xml.etree import ElementTree as ET

BDIF = "https://bdif.amf-france.org"
API = f"{BDIF}/back/api/v1"
JETON_CEG = "RS00002627"
JETON_BNP = "RS00003376"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
})

# ──────────────────────────────────────────────────────────────────────────────
print("1 — Full RSS XML dump (all namespaces, all elements per item)")
print("─" * 70)

r = session.get(f"{API}/rss?lang=en&jetons={JETON_BNP}", timeout=15)
print(f"Status: {r.status_code}  Len: {len(r.text)}")

# Print first item in full
items_raw = re.findall(r"<item>(.*?)</item>", r.text, re.S)
if items_raw:
    print(f"\nTotal items: {len(items_raw)}")
    print(f"\nFirst item (full XML):\n{items_raw[0]}")
    print(f"\nSecond item (full XML):\n{items_raw[1] if len(items_raw) > 1 else 'N/A'}")

# Look for any hash-like strings (64 hex chars)
hashes = re.findall(r"[0-9A-Fa-f]{64}", r.text)
print(f"\n64-char hex strings in RSS: {hashes[:5]}")

# Look for any URL containing /documents/
doc_urls = re.findall(r"https?://[^\s<>\"']+/documents/[^\s<>\"']+", r.text)
print(f"\nDocument URLs in RSS: {doc_urls[:5]}")

# ──────────────────────────────────────────────────────────────────────────────
print("\n\n2 — RSS with extra params (maybe hash comes with additional query params)")
print("─" * 70)

for extra_params in [
    {"lang": "en", "jetons": JETON_BNP, "withFiles": "true"},
    {"lang": "en", "jetons": JETON_BNP, "withDocuments": "true"},
    {"lang": "en", "jetons": JETON_BNP, "includeFiles": "true"},
    {"lang": "en", "jetons": JETON_BNP, "full": "true"},
    {"lang": "en", "jetons": JETON_BNP, "format": "full"},
]:
    r2 = session.get(f"{API}/rss", params=extra_params, timeout=12)
    hashes2 = re.findall(r"[0-9A-Fa-f]{64}", r2.text)
    doc_urls2 = re.findall(r"/documents/[^\s<>\"']+", r2.text)
    changed = len(r2.text) != len(r.text)
    print(f"\nParams {list(extra_params.keys())[-1]}: Len={len(r2.text)} {'CHANGED' if changed else 'same'}")
    if hashes2:
        print(f"  Hashes: {hashes2[:3]}")
    if doc_urls2:
        print(f"  Doc URLs: {doc_urls2[:3]}")

# ──────────────────────────────────────────────────────────────────────────────
print("\n\n3 — Check if hash can be derived from doc_id deterministically")
print("─" * 70)

# Known: doc_id=2026DD1098799, hash=3C2A8CF4D77130CF3FF44F65B2F9939E7EFA897BBD767ECBB4417AA85937A2CD
# Test: is it SHA256 of the doc_id string or some variant?

import hashlib

doc_id = "2026DD1098799"
known_hash = "3C2A8CF4D77130CF3FF44F65B2F9939E7EFA897BBD767ECBB4417AA85937A2CD"

candidates = [
    doc_id,
    doc_id.upper(),
    doc_id.lower(),
    f"DD_{doc_id}",
    f"2026/{doc_id}",
    doc_id.replace("2026DD", "DD_26_"),
    "DD_26_1098799",
    "1098799",
    "DD_26_1098799_12032410.pdf",
    "DD_26_1098799_12032410",
    f"documents/2026/{doc_id}",
]

print(f"Known hash: {known_hash}")
print(f"Checking SHA256 of various inputs:")
for c in candidates:
    h = hashlib.sha256(c.encode()).hexdigest().upper()
    match = "✓ MATCH!" if h == known_hash else ""
    print(f"  sha256({c!r}) = {h[:16]}... {match}")