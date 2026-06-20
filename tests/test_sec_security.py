"""Policy tests for hardened SEC ingestion boundaries."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import math
from typing import Any, cast

import pytest

from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecResourceLimits,
    SecResourceProfile,
    SecSecurityPolicy,
    SecSecurityReason,
)


MIB = 1024 * 1024


def test_resource_profiles_and_reason_codes_are_stable_enums() -> None:
    assert {profile.value for profile in SecResourceProfile} == {
        "daily_index",
        "json_metadata",
        "filing_document",
    }
    assert {reason.value for reason in SecSecurityReason} == {
        "host",
        "redirect",
        "content_type",
        "response_size",
        "cache_path",
        "xml",
        "zip",
    }
    assert SecResourceProfile.__slots__ == ()
    assert SecSecurityReason.__slots__ == ()


def test_policy_value_types_are_frozen_and_slotted() -> None:
    limits = SecResourceLimits(frozenset({"text/plain"}), 1)
    policy = DEFAULT_SEC_SECURITY_POLICY

    assert isinstance(policy, SecSecurityPolicy)
    assert not hasattr(limits, "__dict__")
    assert not hasattr(policy, "__dict__")
    with pytest.raises(FrozenInstanceError):
        limits.max_bytes = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        policy.max_redirects = 1  # type: ignore[misc]
    with pytest.raises(AttributeError):
        SecResourceProfile.DAILY_INDEX = "changed"  # type: ignore[misc]


def test_default_policy_matches_locked_security_limits() -> None:
    policy = DEFAULT_SEC_SECURITY_POLICY

    assert policy.allowed_hosts == frozenset({"www.sec.gov", "data.sec.gov"})
    assert policy.max_redirects == 3
    assert policy.request_timeout_seconds == (15.0, 15.0)
    assert policy.response_chunk_bytes == 64 * 1024
    assert policy.cache_freshness_seconds == 24 * 60 * 60
    assert policy.xml_max_bytes == 8 * MIB
    assert policy.xml_max_elements == 100_000
    assert policy.xml_max_depth == 64
    assert policy.xml_max_text_bytes == 2 * MIB
    assert policy.xml_max_scalar_chars == 4_096
    assert policy.xml_max_long_text_chars == 256 * 1024
    assert policy.xml_max_numeric_chars == 128
    assert policy.zip_max_member_name_chars == 512
    assert policy.zip_max_entries == 2_000_000
    assert policy.zip_max_member_bytes == 64 * MIB
    assert policy.zip_max_total_bytes == 16 * 1024 * MIB
    assert policy.zip_max_compression_ratio == 200.0


def test_default_resource_profiles_have_expected_media_types_and_limits() -> None:
    policy = DEFAULT_SEC_SECURITY_POLICY

    assert policy.limits_for(SecResourceProfile.DAILY_INDEX) == SecResourceLimits(
        frozenset({"text/plain"}), 32 * MIB
    )
    assert policy.limits_for(SecResourceProfile.JSON_METADATA) == SecResourceLimits(
        frozenset({"application/json", "text/plain"}), 32 * MIB
    )
    assert policy.limits_for(SecResourceProfile.FILING_DOCUMENT) == SecResourceLimits(
        frozenset(
            {
                "text/plain",
                "application/xml",
                "text/xml",
                "application/octet-stream",
            }
        ),
        32 * MIB,
    )


@pytest.mark.parametrize(
    "invalid_hosts",
    [
        set(),
        {""},
        {"WWW.sec.gov"},
        {"*.sec.gov"},
        {"www.sec.gov:443"},
        {"sec_gov.example"},
        {"sec..gov"},
        {"séc.gov"},
        {42},
    ],
)
def test_policy_rejects_invalid_host_collections(invalid_hosts: object) -> None:
    with pytest.raises(ValueError, match="host"):
        replace(
            DEFAULT_SEC_SECURITY_POLICY,
            allowed_hosts=cast(Any, invalid_hosts),
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("max_redirects", True),
        ("max_redirects", 0),
        ("response_chunk_bytes", -1),
        ("cache_freshness_seconds", math.inf),
        ("xml_max_bytes", 0),
        ("xml_max_elements", 1.5),
        ("xml_max_depth", False),
        ("xml_max_text_bytes", -1),
        ("xml_max_scalar_chars", 0),
        ("xml_max_long_text_chars", 0),
        ("xml_max_numeric_chars", 0),
        ("zip_max_member_name_chars", 0),
        ("zip_max_entries", math.nan),
        ("zip_max_member_bytes", -1),
        ("zip_max_total_bytes", 0),
    ],
)
def test_policy_rejects_nonpositive_or_nonfinite_values(
    field_name: str, invalid_value: object
) -> None:
    with pytest.raises(ValueError):
        replace(
            DEFAULT_SEC_SECURITY_POLICY,
            **cast(Any, {field_name: invalid_value}),
        )


@pytest.mark.parametrize(
    "invalid_timeout",
    [
        None,
        True,
        15.0,
        (15.0,),
        (15.0, 15.0, 15.0),
        (True, 15.0),
        (0.0, 15.0),
        (math.inf, 15.0),
        (math.nan, 15.0),
    ],
)
def test_policy_rejects_invalid_timeout_tuple(invalid_timeout: object) -> None:
    with pytest.raises(ValueError, match="timeout"):
        replace(
            DEFAULT_SEC_SECURITY_POLICY,
            request_timeout_seconds=cast(Any, invalid_timeout),
        )


@pytest.mark.parametrize(
    "invalid_ratio",
    [None, True, "200", 1.0, 0.0, -1.0, math.inf, -math.inf, math.nan],
)
def test_policy_rejects_invalid_compression_ratio(invalid_ratio: object) -> None:
    with pytest.raises(ValueError, match="ratio"):
        replace(
            DEFAULT_SEC_SECURITY_POLICY,
            zip_max_compression_ratio=cast(Any, invalid_ratio),
        )


@pytest.mark.parametrize(
    "invalid_media_types",
    [
        set(),
        {"Text/Plain"},
        {"text/plain; charset=utf-8"},
        {" text/plain"},
        {"textplain"},
        {"téxt/plain"},
        {42},
    ],
)
def test_resource_limits_reject_invalid_media_types(
    invalid_media_types: object,
) -> None:
    with pytest.raises(ValueError, match="media type"):
        SecResourceLimits(cast(Any, invalid_media_types), 1)


@pytest.mark.parametrize(
    "invalid_max_bytes", [None, True, 0, -1, 1.5, math.inf, math.nan]
)
def test_resource_limits_reject_invalid_byte_limits(
    invalid_max_bytes: object,
) -> None:
    with pytest.raises(ValueError, match="max_bytes"):
        SecResourceLimits(frozenset({"text/plain"}), cast(Any, invalid_max_bytes))


def test_policy_rejects_incomplete_or_malformed_profile_limits() -> None:
    missing_profile = dict(DEFAULT_SEC_SECURITY_POLICY.resource_limits)
    del missing_profile[SecResourceProfile.DAILY_INDEX]

    with pytest.raises(ValueError, match="resource profile"):
        replace(DEFAULT_SEC_SECURITY_POLICY, resource_limits=missing_profile)
    with pytest.raises(ValueError, match="resource profile"):
        replace(
            DEFAULT_SEC_SECURITY_POLICY,
            resource_limits=cast(Any, {"daily_index": object()}),
        )


def test_constructor_copies_collections_into_immutable_values() -> None:
    hosts = {"www.sec.gov"}
    media_types = {"text/plain"}
    limits = SecResourceLimits(cast(Any, media_types), 10)
    profiles = {
        profile: limits
        for profile in SecResourceProfile
    }

    policy = replace(
        DEFAULT_SEC_SECURITY_POLICY,
        allowed_hosts=cast(Any, hosts),
        resource_limits=profiles,
    )
    hosts.add("data.sec.gov")
    media_types.add("application/json")
    profiles[SecResourceProfile.DAILY_INDEX] = SecResourceLimits(
        frozenset({"application/json"}), 20
    )

    assert policy.allowed_hosts == frozenset({"www.sec.gov"})
    assert limits.allowed_media_types == frozenset({"text/plain"})
    assert policy.limits_for(SecResourceProfile.DAILY_INDEX) == limits
    with pytest.raises(TypeError):
        policy.resource_limits[SecResourceProfile.DAILY_INDEX] = limits  # type: ignore[index]
