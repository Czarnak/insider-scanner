#!/usr/bin/env python3
"""Update the runtime Congress member list with current legislators.

Federal legislators are fetched from the unitedstates/congress-legislators
GitHub repository (public domain, maintained by civic tech community).
Committee assignments and sector mappings are derived from the same source.

State legislators can optionally be fetched from the Open States API
(https://v3.openstates.org) — requires a free API key set via
OPENSTATES_API_KEY environment variable.

Usage:
    # Federal only (no API key needed)
    python scripts/update_congress.py

    # Federal + state legislators
    OPENSTATES_API_KEY=your_key python scripts/update_congress.py --include-state

    # Dry run — print to stdout without writing file
    python scripts/update_congress.py --dry-run

    # Custom output path
    python scripts/update_congress.py --output /path/to/members.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

import requests
import yaml

# Add project root to path so we can import project modules
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root / "src"))

from insider_scanner.utils.atomic_file import atomic_write_bytes
from insider_scanner.utils.config import CONGRESS_FILE
from insider_scanner.utils.http import HttpSession

FEDERAL_URL = (
    "https://raw.githubusercontent.com/unitedstates/"
    "congress-legislators/main/legislators-current.yaml"
)
FEDERAL_FALLBACK_URL = (
    "https://raw.githubusercontent.com/unitedstates/"
    "congress-legislators/main/legislators-current.json"
)
COMMITTEES_URL = (
    "https://raw.githubusercontent.com/unitedstates/"
    "congress-legislators/main/committees-current.yaml"
)
MEMBERSHIP_URL = (
    "https://raw.githubusercontent.com/unitedstates/"
    "congress-legislators/main/committee-membership-current.yaml"
)
OPENSTATES_PEOPLE_URL = "https://v3.openstates.org/people"

DEFAULT_OUTPUT = CONGRESS_FILE
SEED_OUTPUT = (
    _project_root
    / "src"
    / "insider_scanner"
    / "resources"
    / "seeds"
    / "congress_members.json"
)


OPENSTATES_JURISDICTIONS = tuple(
    [
        "al",
        "ak",
        "az",
        "ar",
        "ca",
        "co",
        "ct",
        "de",
        "fl",
        "ga",
        "hi",
        "id",
        "il",
        "in",
        "ia",
        "ks",
        "ky",
        "la",
        "me",
        "md",
        "ma",
        "mi",
        "mn",
        "ms",
        "mo",
        "mt",
        "ne",
        "nv",
        "nh",
        "nj",
        "nm",
        "ny",
        "nc",
        "nd",
        "oh",
        "ok",
        "or",
        "pa",
        "ri",
        "sc",
        "sd",
        "tn",
        "tx",
        "ut",
        "vt",
        "va",
        "wa",
        "wv",
        "wi",
        "wy",
    ]
)
STATE_REQUEST_INTERVAL = 6.1  # Open States' default tier allows 10 requests/minute.


class StateFetchError(ValueError):
    """An incomplete state snapshot must never replace the saved member list."""


# -----------------------------------------------------------------------
# Committee -> Sector mapping
# -----------------------------------------------------------------------

# Maps committee name keywords to sector labels. First match wins.
# Order matters: more specific keywords should come before generic ones.
COMMITTEE_SECTOR_MAP: dict[str, str] = {
    "armed services": "Defense",
    "veterans": "Defense",
    "intelligence": "Defense",
    "homeland security": "Defense",
    "energy": "Energy",
    "natural resources": "Energy",
    "environment": "Energy",
    "financial services": "Finance",
    "banking": "Finance",
    "finance": "Finance",
    "budget": "Finance",
    "ways and means": "Finance",
    "appropriations": "Finance",
    "science": "Technology",
    "technology": "Technology",
    "commerce": "Technology",
    "health": "Healthcare",
    "agriculture": "Other",
    "education": "Other",
    "judiciary": "Other",
    "foreign affairs": "Other",
    "foreign relations": "Other",
    "oversight": "Other",
    "rules": "Other",
    "ethics": "Other",
    "administration": "Other",
    "small business": "Other",
    "transportation": "Industrials",
    "infrastructure": "Industrials",
}


def map_committee_to_sector(committee_name: str) -> str:
    """Map a committee name to a market sector.

    Returns the sector string (e.g. "Defense", "Finance") or "Other"
    if no keyword match is found.
    """
    name_lower = committee_name.lower()
    for keyword, sector in COMMITTEE_SECTOR_MAP.items():
        if keyword in name_lower:
            return sector
    return "Other"


def determine_sectors(committees: list[str]) -> list[str]:
    """Determine all relevant sectors from a list of committee names.

    Returns a deduplicated list of sector strings, ordered by priority:
    Defense > Energy > Finance > Technology > Healthcare > Industrials > Other

    "Other" is only included if no higher-priority sector was found.
    """
    sector_priority = [
        "Defense",
        "Energy",
        "Finance",
        "Technology",
        "Healthcare",
        "Industrials",
        "Other",
    ]

    raw_sectors = {map_committee_to_sector(c) for c in committees}

    # If we have any real sector, drop "Other"
    if raw_sectors - {"Other"}:
        raw_sectors.discard("Other")

    # Return in priority order
    return [s for s in sector_priority if s in raw_sectors] or ["Other"]


# -----------------------------------------------------------------------
# Committee data fetching
# -----------------------------------------------------------------------


def fetch_committees() -> dict[str, str]:
    """Fetch current committee definitions and return {committee_id: name}.

    Uses committees-current.yaml from the congress-legislators repo.
    Returns a mapping like {"HSAG": "Agriculture", "SSAS": "Armed Services"}.
    """
    try:
        print("Fetching committee definitions...")
        resp = requests.get(COMMITTEES_URL, timeout=30)
        resp.raise_for_status()

        raw = yaml.safe_load(resp.text)

        committees = {}
        for committee in raw:
            thomas_id = committee.get("thomas_id", "")
            name = committee.get("name", "")
            if thomas_id and name:
                committees[thomas_id] = name
                # Also index subcommittees
                for sub in committee.get("subcommittees", []):
                    sub_id = thomas_id + sub.get("thomas_id", "")
                    sub_name = sub.get("name", name)
                    committees[sub_id] = sub_name

        print(f"  Found {len(committees)} committees + subcommittees")
        return committees

    except Exception as exc:  # noqa: BLE001 - optional enrichment must not abort an update
        print(f"  Failed to fetch committees: {exc}")
        return {}


def fetch_committee_membership() -> dict[str, list[str]]:
    """Fetch committee membership and return {bioguide_id: [committee_ids]}.

    Uses committee-membership-current.yaml from the congress-legislators repo.
    """
    try:
        print("Fetching committee membership...")
        resp = requests.get(MEMBERSHIP_URL, timeout=30)
        resp.raise_for_status()

        raw = yaml.safe_load(resp.text)

        # raw is {committee_id: [{bioguide: ..., name: ..., ...}, ...]}
        bioguide_to_committees: dict[str, list[str]] = {}
        for committee_id, members in raw.items():
            if not isinstance(members, list):
                continue
            for member in members:
                bio_id = member.get("bioguide", "")
                if bio_id:
                    bioguide_to_committees.setdefault(bio_id, [])
                    bioguide_to_committees[bio_id].append(committee_id)

        print(f"  Found membership data for {len(bioguide_to_committees)} legislators")
        return bioguide_to_committees

    except Exception as exc:  # noqa: BLE001 - optional enrichment must not abort an update
        print(f"  Failed to fetch committee membership: {exc}")
        return {}


def enrich_with_committees(
    members: list[dict],
    committees: dict[str, str],
    membership: dict[str, list[str]],
) -> None:
    """Add 'committees' and 'sector' fields to each member dict in-place.

    Parameters
    ----------
    members : list of dict
        Legislator dicts with at least a 'bioguide_id' field.
    committees : dict
        Mapping of committee_id -> committee name.
    membership : dict
        Mapping of bioguide_id -> list of committee_ids.
    """
    if not committees or not membership:
        print("  Skipping committee enrichment (missing data)")
        for m in members:
            m.setdefault("committees", [])
            m.setdefault("sector", ["Other"])
        return

    enriched = 0
    for m in members:
        bio_id = m.get("bioguide_id", "")
        member_committee_ids = membership.get(bio_id, [])

        # Resolve IDs to names, deduplicating parent committees
        seen_names = set()
        committee_names = []
        for cid in member_committee_ids:
            # Use parent committee (first 4 chars) for sector mapping
            parent_id = cid[:4] if len(cid) > 4 else cid
            name = committees.get(parent_id, committees.get(cid, ""))
            if name and name not in seen_names:
                seen_names.add(name)
                committee_names.append(name)

        m["committees"] = committee_names
        m["sector"] = determine_sectors(committee_names)

        if committee_names:
            enriched += 1

    print(f"  Enriched {enriched}/{len(members)} legislators with committee data")


# -----------------------------------------------------------------------
# Federal legislators (GitHub -- no API key needed)
# -----------------------------------------------------------------------


def _member_from_legislator(person: dict) -> dict:
    """Build one federal member identically for both supported source formats."""
    name = person["name"]
    term = person["terms"][-1]
    first, last = name["first"].strip(), name["last"].strip()
    if not first or not last or term["type"] not in {"sen", "rep"}:
        raise ValueError("Invalid federal member identity")
    return {
        "name": f"{last} {first}",
        "first_name": first,
        "last_name": last,
        "official_name": name.get("official_full") or f"{first} {last}",
        "state": term.get("state", ""),
        "chamber": "Senate" if term["type"] == "sen" else "House",
        "party": term.get("party", ""),
        "level": "federal",
        "bioguide_id": person["id"]["bioguide"],
    }


def fetch_federal_legislators() -> list[dict]:
    """Return a complete validated source, or [] if neither source succeeds."""
    for url, label in ((FEDERAL_URL, "YAML"), (FEDERAL_FALLBACK_URL, "JSON")):
        try:
            print(f"Fetching federal legislators from GitHub ({label})...")
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            raw = yaml.safe_load(response.text) if label == "YAML" else response.json()
            if not isinstance(raw, list) or not raw:
                raise ValueError("Federal source is empty or is not a list")
            # Build a fresh list so a malformed YAML row cannot leak into fallback.
            members = [_member_from_legislator(person) for person in raw]
            print(f"  Found {len(members)} federal legislators")
            return members
        except (
            requests.RequestException,
            yaml.YAMLError,
            ValueError,
            KeyError,
            TypeError,
            IndexError,
        ):
            print(f"  {label} source failed validation or could not be fetched")
    return []


# -----------------------------------------------------------------------
# State legislators (Open States API -- requires free API key)
# -----------------------------------------------------------------------


def _state_member(person: dict, classification: str) -> dict | None:
    role = person.get("current_role") or {}
    if role.get("org_classification") != classification:
        return None
    first = (person.get("given_name") or "").strip()
    last = (person.get("family_name") or "").strip()
    name = (person.get("name") or "").strip()
    if not first or not last:
        if ", " in name:
            last, first = name.split(", ", 1)
        else:
            parts = name.rsplit(" ", 1)
            if len(parts) != 2:
                raise ValueError("State member lacks structured or parseable name")
            first, last = parts
    if not person.get("id") or not first or not last:
        raise ValueError("State member lacks an identity")
    return {
        "name": f"{last} {first}",
        "first_name": first,
        "last_name": last,
        "official_name": name or f"{first} {last}",
        "state": (person.get("jurisdiction") or {}).get("name", ""),
        "chamber": "State Senate" if classification == "upper" else "State House",
        "party": person.get("party", ""),
        "level": "state",
        "openstates_id": person["id"],
        "committees": [],
        "sector": ["Other"],
    }


def fetch_state_legislators(api_key: str) -> list[dict]:
    """Fetch complete state/chamber pages with shared bounded HTTP retries.

    The API requires jurisdiction/name/id and accepts at most 50 rows/page.
    A failed jurisdiction is counted while others are checked; an incomplete
    snapshot raises StateFetchError so main cannot publish its partial members.
    """
    members: dict[str, dict] = {}
    failures = []
    print("Fetching state legislators from Open States API...")
    with HttpSession(min_interval_seconds=STATE_REQUEST_INTERVAL) as client:
        for jurisdiction in OPENSTATES_JURISDICTIONS:
            classifications = ("upper",) if jurisdiction == "ne" else ("upper", "lower")
            for classification in classifications:
                try:
                    page = 1
                    while True:
                        params = {
                            "jurisdiction": jurisdiction,
                            "org_classification": classification,
                            "page": page,
                            "per_page": 50,
                        }
                        response = client.get(
                            OPENSTATES_PEOPLE_URL + "?" + urlencode(params),
                            headers={"X-API-KEY": api_key},
                            timeout=30,
                        )
                        data = response.json()
                        results = data["results"]
                        max_page = int(data["pagination"]["max_page"])
                        if not isinstance(results, list) or max_page > 80:
                            raise ValueError("Invalid or unbounded state pagination")
                        if not results and page < max_page:
                            raise ValueError(
                                "State pagination ended before its final page"
                            )
                        for person in results:
                            member = _state_member(person, classification)
                            if member is not None:
                                members[member["openstates_id"]] = member
                        if page >= max_page:
                            break
                        page += 1
                except requests.HTTPError as exc:
                    status = (
                        exc.response.status_code if exc.response is not None else None
                    )
                    if status in {401, 403, 429}:
                        # Invalid credentials or exhausted quota will affect every state.
                        raise StateFetchError(
                            f"Open States stopped with HTTP {status}; no update was saved"
                        ) from None
                    failures.append(f"{jurisdiction}/{classification}")
                except (requests.RequestException, ValueError, KeyError, TypeError):
                    failures.append(f"{jurisdiction}/{classification}")
    if failures:
        raise StateFetchError(
            f"Open States failed for {len(failures)} jurisdiction/chamber requests; "
            "no update was saved"
        )
    print(f"  Found {len(members)} state legislators")
    return list(members.values())


# -----------------------------------------------------------------------
# Merge and save
# -----------------------------------------------------------------------


def merge_and_save(
    federal: list[dict],
    state: list[dict],
    output_path: Path,
    dry_run: bool = False,
) -> None:
    """Merge federal + state lists and write to JSON."""
    if not federal:
        raise ValueError("Refusing to replace the member list without federal members")
    previous = []
    if output_path.exists():
        previous = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(previous, list) or any(
            not isinstance(m, dict) for m in previous
        ):
            raise ValueError(
                "Existing member list is invalid; refusing to overwrite it"
            )
        old_federal = sum(m.get("level", "federal") == "federal" for m in previous)
        if len(federal) < old_federal * 0.8:
            raise ValueError("Federal result is much smaller than the existing list")
        old_state = sum(m.get("level") == "state" for m in previous)
        if state and len(state) < old_state * 0.8:
            raise ValueError("State result is much smaller than the existing list")
    # A federal-only refresh must retain the user's previously fetched state data.
    retained_state = (
        [m for m in previous if m.get("level") == "state"] if not state else []
    )
    combined = federal + state + retained_state

    # Sort by level (federal first), then state, then name
    combined.sort(
        key=lambda m: (
            0 if m.get("level") == "federal" else 1,
            m.get("state", ""),
            m.get("name", ""),
        )
    )

    # Deduplicate by (name, state) -- shouldn't happen but just in case
    seen = set()
    deduped = []
    for m in combined:
        key = (
            m.get("level", "federal"),
            m.get("bioguide_id")
            or m.get("openstates_id")
            or (m["name"].casefold(), m.get("state", "").casefold()),
        )
        if key not in seen:
            seen.add(key)
            deduped.append(m)

    # Summary
    federal_count = sum(1 for m in deduped if m.get("level") == "federal")
    state_count = sum(1 for m in deduped if m.get("level") == "state")

    # Sector distribution for federal
    sector_counts: dict[str, int] = {}
    for m in deduped:
        if m.get("level") == "federal":
            for s in m.get("sector", ["Other"]):
                sector_counts[s] = sector_counts.get(s, 0) + 1

    print(
        f"\nTotal: {len(deduped)} legislators ({federal_count} federal, {state_count} state)"
    )
    if sector_counts:
        print("Sector distribution (federal):")
        for sector, count in sorted(sector_counts.items(), key=lambda x: -x[1]):
            print(f"  {sector:<20} {count}")

    if dry_run:
        print("\n--- DRY RUN (first 20 entries) ---")
        for m in deduped[:20]:
            committees = ", ".join(m.get("committees", [])[:2])
            if len(m.get("committees", [])) > 2:
                committees += ", ..."
            sectors = ", ".join(m.get("sector", ["Other"]))
            print(
                f"  [{m.get('level', '?'):>7}] {m['name']:<30} "
                f"{m.get('state', ''):>2}  {m.get('chamber', ''):<15} "
                f"{m.get('party', ''):<12} sector=[{sectors}]  "
                f"committees=[{committees}]"
            )
        if len(deduped) > 20:
            print(f"  ... and {len(deduped) - 20} more")
        return

    atomic_write_bytes(
        output_path, (json.dumps(deduped, indent=2) + "\n").encode("utf-8")
    )
    print(f"Saved to: {output_path}")


# -----------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Update congress_members.json with current legislators.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/update_congress.py                     # Federal only
  python scripts/update_congress.py --include-state     # Federal + state (needs OPENSTATES_API_KEY)
  python scripts/update_congress.py --dry-run           # Preview without saving
  python scripts/update_congress.py --no-committees     # Skip committee enrichment
        """,
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output JSON file path (default: application runtime member list)",
    )
    parser.add_argument(
        "--include-state",
        action="store_true",
        help="Include state legislators (requires OPENSTATES_API_KEY env var)",
    )
    parser.add_argument(
        "--no-committees",
        action="store_true",
        help="Skip committee assignment and sector enrichment",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results without saving to file",
    )
    parser.add_argument(
        "--update-seed",
        action="store_true",
        help="Update the authoritative packaged seed instead of the runtime file",
    )
    args = parser.parse_args(argv)
    if args.update_seed:
        if args.output != DEFAULT_OUTPUT:
            parser.error("--update-seed cannot be combined with --output")
        args.output = SEED_OUTPUT

    # Fetch federal
    federal = fetch_federal_legislators()
    if not federal:
        print("ERROR: Could not fetch federal legislators; no file was changed.")
        return 1

    # Enrich with committee data (unless skipped)
    if not args.no_committees and federal:
        committees = fetch_committees()
        membership = fetch_committee_membership()
        if not committees or not membership:
            print(
                "ERROR: Committee enrichment failed; no file was changed. Use --no-committees to skip it explicitly."
            )
            return 1
        enrich_with_committees(federal, committees, membership)
    else:
        for m in federal:
            m.setdefault("committees", [])
            m.setdefault("sector", ["Other"])

    # Requested state data is required for this snapshot, never silently skipped.
    state = []
    if args.include_state:
        api_key = os.environ.get("OPENSTATES_API_KEY", "").strip()
        if not api_key:
            print(
                "ERROR: OPENSTATES_API_KEY is required for --include-state; no file was changed."
            )
            return 1
        try:
            state = fetch_state_legislators(api_key)
        except StateFetchError as exc:
            print(f"ERROR: {exc}")
            return 1
        if not state:
            print("ERROR: Open States returned no legislators; no file was changed.")
            return 1

    try:
        merge_and_save(federal, state, args.output, dry_run=args.dry_run)
    except (OSError, ValueError) as exc:
        print(f"ERROR: Member update was not saved: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
