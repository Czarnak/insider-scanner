"""Tests for the Congress member update script."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import responses

# Add scripts/ to path so we can import the module
_scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts_dir))

import update_congress

SAMPLE_YAML = """
- id:
    bioguide: A000001
  name:
    first: Alice
    last: Smith
    official_full: Alice Smith
  terms:
    - type: sen
      state: CA
      party: Democrat
- id:
    bioguide: B000002
  name:
    first: Bob
    last: Jones
    official_full: Bob Jones
  terms:
    - type: rep
      state: TX
      party: Republican
"""

SAMPLE_COMMITTEES_YAML = """
- type: house
  name: Armed Services
  thomas_id: HSAS
  subcommittees:
    - name: Tactical Air and Land Forces
      thomas_id: "28"
- type: house
  name: Financial Services
  thomas_id: HSBA
- type: senate
  name: Energy and Natural Resources
  thomas_id: SSEG
"""

SAMPLE_MEMBERSHIP_YAML = """
HSAS:
  - name: Alice Smith
    bioguide: A000001
    party: majority
    rank: 1
HSBA:
  - name: Bob Jones
    bioguide: B000002
    party: majority
    rank: 1
HSAS28:
  - name: Alice Smith
    bioguide: A000001
    party: majority
    rank: 1
SSEG:
  - name: Alice Smith
    bioguide: A000001
    party: minority
    rank: 3
"""

SAMPLE_OPENSTATES_PAGE1 = {
    "results": [
        {
            "name": "Carlos Rivera",
            "jurisdiction": {"name": "California"},
            "current_role": {"org_classification": "upper"},
            "party": "Democrat",
            "id": "ocd-person/111",
        },
    ],
    "pagination": {"max_page": 1},
}


class TestFetchFederalLegislators:
    @responses.activate
    def test_yaml_source(self):
        responses.add(
            responses.GET,
            update_congress.FEDERAL_URL,
            body=SAMPLE_YAML,
            status=200,
        )
        members = update_congress.fetch_federal_legislators()
        assert len(members) == 2
        assert members[0]["last_name"] == "Smith"
        assert members[0]["chamber"] == "Senate"
        assert members[0]["state"] == "CA"
        assert members[0]["level"] == "federal"
        assert members[1]["chamber"] == "House"
        assert members[1]["state"] == "TX"

    @responses.activate
    def test_yaml_fail_json_fallback(self):
        responses.add(responses.GET, update_congress.FEDERAL_URL, status=500)
        responses.add(
            responses.GET,
            update_congress.FEDERAL_FALLBACK_URL,
            json=[
                {
                    "id": {"bioguide": "C000003"},
                    "name": {
                        "first": "Carol",
                        "last": "Lee",
                        "official_full": "Carol Lee",
                    },
                    "terms": [{"type": "sen", "state": "NY", "party": "Democrat"}],
                }
            ],
            status=200,
        )
        members = update_congress.fetch_federal_legislators()
        assert len(members) == 1
        assert members[0]["last_name"] == "Lee"

    @responses.activate
    def test_both_fail(self):
        responses.add(responses.GET, update_congress.FEDERAL_URL, status=500)
        responses.add(responses.GET, update_congress.FEDERAL_FALLBACK_URL, status=500)
        members = update_congress.fetch_federal_legislators()
        assert members == []

    @responses.activate
    def test_name_format(self):
        responses.add(
            responses.GET,
            update_congress.FEDERAL_URL,
            body=SAMPLE_YAML,
            status=200,
        )
        members = update_congress.fetch_federal_legislators()
        # Name should be "Last First" for congress flagging consistency
        assert members[0]["name"] == "Smith Alice"
        assert members[1]["name"] == "Jones Bob"


class TestFetchStateLegislators:
    @responses.activate
    def test_single_page(self):
        responses.add(
            responses.GET,
            update_congress.OPENSTATES_PEOPLE_URL,
            json=SAMPLE_OPENSTATES_PAGE1,
            status=200,
        )
        members = update_congress.fetch_state_legislators("test-key")
        assert len(members) == 1
        assert members[0]["level"] == "state"
        assert members[0]["chamber"] == "State Senate"
        # State legislators get empty committees and Other sector
        assert members[0]["committees"] == []
        assert members[0]["sector"] == ["Other"]

    @responses.activate
    def test_invalid_api_key(self):
        responses.add(
            responses.GET,
            update_congress.OPENSTATES_PEOPLE_URL,
            status=401,
        )
        members = update_congress.fetch_state_legislators("bad-key")
        assert members == []

    @responses.activate
    def test_empty_results(self):
        responses.add(
            responses.GET,
            update_congress.OPENSTATES_PEOPLE_URL,
            json={"results": [], "pagination": {"max_page": 1}},
            status=200,
        )
        members = update_congress.fetch_state_legislators("test-key")
        assert members == []


class TestCommitteeSectorMapping:
    def test_armed_services(self):
        assert update_congress.map_committee_to_sector("Armed Services") == "Defense"

    def test_homeland_security(self):
        assert update_congress.map_committee_to_sector("Homeland Security") == "Defense"

    def test_financial_services(self):
        assert (
            update_congress.map_committee_to_sector("Financial Services") == "Finance"
        )

    def test_banking(self):
        assert (
            update_congress.map_committee_to_sector(
                "Banking, Housing, and Urban Affairs"
            )
            == "Finance"
        )

    def test_energy(self):
        assert (
            update_congress.map_committee_to_sector("Energy and Natural Resources")
            == "Energy"
        )

    def test_science_technology(self):
        assert (
            update_congress.map_committee_to_sector("Science, Space, and Technology")
            == "Technology"
        )

    def test_health(self):
        assert (
            update_congress.map_committee_to_sector(
                "Health, Education, Labor, and Pensions"
            )
            == "Healthcare"
        )

    def test_transportation(self):
        assert (
            update_congress.map_committee_to_sector("Transportation and Infrastructure")
            == "Industrials"
        )

    def test_unknown(self):
        assert (
            update_congress.map_committee_to_sector("Completely Unknown Committee")
            == "Other"
        )

    def test_case_insensitive(self):
        assert update_congress.map_committee_to_sector("ARMED SERVICES") == "Defense"


class TestDetermineSectors:
    def test_defense_and_finance(self):
        committees = ["Armed Services", "Financial Services"]
        assert update_congress.determine_sectors(committees) == ["Defense", "Finance"]

    def test_energy_and_technology(self):
        committees = ["Energy and Natural Resources", "Science, Space, and Technology"]
        assert update_congress.determine_sectors(committees) == ["Energy", "Technology"]

    def test_single_committee(self):
        assert update_congress.determine_sectors(["Financial Services"]) == ["Finance"]

    def test_empty_list(self):
        assert update_congress.determine_sectors([]) == ["Other"]

    def test_all_other_committees(self):
        committees = ["Judiciary", "Education and the Workforce"]
        assert update_congress.determine_sectors(committees) == ["Other"]

    def test_other_excluded_when_real_sectors_present(self):
        """Judiciary (Other) + Armed Services (Defense) → only Defense."""
        committees = ["Judiciary", "Armed Services"]
        assert update_congress.determine_sectors(committees) == ["Defense"]

    def test_priority_order_preserved(self):
        """Sectors returned in priority order regardless of input order."""
        committees = [
            "Financial Services",
            "Armed Services",
            "Energy and Natural Resources",
        ]
        assert update_congress.determine_sectors(committees) == [
            "Defense",
            "Energy",
            "Finance",
        ]


class TestFetchCommittees:
    @responses.activate
    def test_fetch_committees(self):
        responses.add(
            responses.GET,
            update_congress.COMMITTEES_URL,
            body=SAMPLE_COMMITTEES_YAML,
            status=200,
        )
        result = update_congress.fetch_committees()
        assert "HSAS" in result
        assert result["HSAS"] == "Armed Services"
        assert "HSBA" in result
        assert result["HSBA"] == "Financial Services"
        # Subcommittee should be indexed
        assert "HSAS28" in result

    @responses.activate
    def test_fetch_committees_failure(self):
        responses.add(responses.GET, update_congress.COMMITTEES_URL, status=500)
        result = update_congress.fetch_committees()
        assert result == {}


class TestFetchCommitteeMembership:
    @responses.activate
    def test_fetch_membership(self):
        responses.add(
            responses.GET,
            update_congress.MEMBERSHIP_URL,
            body=SAMPLE_MEMBERSHIP_YAML,
            status=200,
        )
        result = update_congress.fetch_committee_membership()
        assert "A000001" in result
        # Alice is on HSAS, HSAS28, and SSEG
        assert "HSAS" in result["A000001"]
        assert "SSEG" in result["A000001"]
        assert "B000002" in result
        assert "HSBA" in result["B000002"]

    @responses.activate
    def test_fetch_membership_failure(self):
        responses.add(responses.GET, update_congress.MEMBERSHIP_URL, status=500)
        result = update_congress.fetch_committee_membership()
        assert result == {}


class TestEnrichWithCommittees:
    def test_enrichment(self):
        members = [
            {"name": "Smith Alice", "bioguide_id": "A000001"},
            {"name": "Jones Bob", "bioguide_id": "B000002"},
            {"name": "Unknown Carol", "bioguide_id": "C000003"},
        ]
        committees = {
            "HSAS": "Armed Services",
            "HSBA": "Financial Services",
            "SSEG": "Energy and Natural Resources",
        }
        membership = {
            "A000001": ["HSAS", "HSAS28", "SSEG"],
            "B000002": ["HSBA"],
        }

        update_congress.enrich_with_committees(members, committees, membership)

        # Alice: Armed Services + Energy → both sectors
        assert "Armed Services" in members[0]["committees"]
        assert "Energy and Natural Resources" in members[0]["committees"]
        assert members[0]["sector"] == ["Defense", "Energy"]

        # Bob: Financial Services → Finance
        assert members[1]["committees"] == ["Financial Services"]
        assert members[1]["sector"] == ["Finance"]

        # Carol: no committees → Other
        assert members[2]["committees"] == []
        assert members[2]["sector"] == ["Other"]

    def test_empty_data_graceful(self):
        members = [{"name": "Test", "bioguide_id": "X000001"}]
        update_congress.enrich_with_committees(members, {}, {})
        assert members[0]["committees"] == []
        assert members[0]["sector"] == ["Other"]

    def test_deduplicates_parent_committees(self):
        """Subcommittee IDs should resolve to parent committee name."""
        members = [{"name": "Test", "bioguide_id": "A000001"}]
        committees = {"HSAS": "Armed Services"}
        # Member sits on parent + subcommittee (same parent)
        membership = {"A000001": ["HSAS", "HSAS28"]}

        update_congress.enrich_with_committees(members, committees, membership)

        # Should not duplicate Armed Services
        assert members[0]["committees"] == ["Armed Services"]
        assert members[0]["sector"] == ["Defense"]


class TestMergeAndSave:
    def test_save(self, tmp_path):
        out = tmp_path / "members.json"
        federal = [
            {
                "name": "Smith Alice",
                "state": "CA",
                "level": "federal",
                "committees": ["Armed Services"],
                "sector": ["Defense"],
            }
        ]
        state = [
            {
                "name": "Rivera Carlos",
                "state": "CA",
                "level": "state",
                "committees": [],
                "sector": ["Other"],
            }
        ]
        update_congress.merge_and_save(federal, state, out)
        data = json.loads(out.read_text())
        assert len(data) == 2
        # Federal should come first
        assert data[0]["level"] == "federal"
        assert data[0]["sector"] == ["Defense"]
        assert data[1]["level"] == "state"

    def test_deduplication(self, tmp_path):
        out = tmp_path / "members.json"
        federal = [
            {
                "name": "Smith Alice",
                "state": "CA",
                "level": "federal",
                "committees": [],
                "sector": ["Other"],
            },
            {
                "name": "Smith Alice",
                "state": "CA",
                "level": "federal",
                "committees": [],
                "sector": ["Other"],
            },
        ]
        update_congress.merge_and_save(federal, [], out)
        data = json.loads(out.read_text())
        assert len(data) == 1

    def test_dry_run(self, tmp_path, capsys):
        out = tmp_path / "members.json"
        update_congress.merge_and_save(
            [
                {
                    "name": "Test",
                    "state": "CA",
                    "level": "federal",
                    "committees": ["Armed Services"],
                    "sector": ["Defense"],
                }
            ],
            [],
            out,
            dry_run=True,
        )
        assert not out.exists()
        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out


@responses.activate
def test_malformed_yaml_rows_do_not_leak_into_json_fallback():
    responses.add(
        responses.GET,
        update_congress.FEDERAL_URL,
        body=SAMPLE_YAML + "- name: {}\n  terms: []\n",
        status=200,
    )
    responses.add(
        responses.GET,
        update_congress.FEDERAL_FALLBACK_URL,
        json=[
            {
                "id": {"bioguide": "C000003"},
                "name": {"first": "Carol", "last": "Lee"},
                "terms": [{"type": "sen"}],
            }
        ],
        status=200,
    )
    members = update_congress.fetch_federal_legislators()
    assert [m["bioguide_id"] for m in members] == ["C000003"]


def test_failed_federal_fetch_leaves_existing_file_unchanged(tmp_path, monkeypatch):
    output = tmp_path / "members.json"
    output.write_text("existing user data", encoding="utf-8")
    monkeypatch.setattr(update_congress, "fetch_federal_legislators", list)
    assert update_congress.main(["--output", str(output)]) == 1
    assert output.read_text(encoding="utf-8") == "existing user data"


def test_smaller_federal_result_is_rejected(tmp_path):
    output = tmp_path / "members.json"
    previous = [{"name": f"Member {i}", "level": "federal"} for i in range(10)]
    original = json.dumps(previous)
    output.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError, match="much smaller"):
        update_congress.merge_and_save([previous[0]], [], output)
    assert output.read_text(encoding="utf-8") == original


def test_federal_only_update_retains_existing_state_members(tmp_path):
    output = tmp_path / "members.json"
    state = {"name": "Rivera Carlos", "level": "state", "state": "CA"}
    output.write_text(json.dumps([state]), encoding="utf-8")
    federal = {"name": "Smith Alice", "level": "federal", "state": "CA"}
    update_congress.merge_and_save([federal], [], output)
    assert state in json.loads(output.read_text(encoding="utf-8"))


def test_atomic_publish_failure_keeps_original_file(tmp_path, monkeypatch):
    output = tmp_path / "members.json"
    output.write_text("[]", encoding="utf-8")
    from insider_scanner.utils import atomic_file

    def fail_replace(*_args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(atomic_file.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        update_congress.merge_and_save(
            [{"name": "Smith Alice", "level": "federal"}], [], output
        )
    assert output.read_text(encoding="utf-8") == "[]"
    assert list(tmp_path.iterdir()) == [output]


def test_output_defaults_to_runtime_and_explicit_seed(monkeypatch):
    member = {"name": "Smith Alice", "level": "federal"}
    monkeypatch.setattr(update_congress, "fetch_federal_legislators", lambda: [member])
    outputs = []
    monkeypatch.setattr(
        update_congress, "merge_and_save", lambda f, s, path, **kw: outputs.append(path)
    )
    assert update_congress.main(["--no-committees"]) == 0
    assert update_congress.main(["--no-committees", "--update-seed"]) == 0
    assert outputs == [update_congress.CONGRESS_FILE, update_congress.SEED_OUTPUT]
