"""Immutable security policy for untrusted SEC ingestion boundaries."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
import math
import re
from types import MappingProxyType


_MIB = 1024 * 1024
_HOST_PATTERN = re.compile(
    r"(?=.{1,253}\Z)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z",
    re.ASCII,
)
_MEDIA_TYPE_PATTERN = re.compile(
    r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*\Z",
    re.ASCII,
)


class SecResourceProfile(StrEnum):
    """SEC response classes with distinct content-type policies."""

    __slots__ = ()

    DAILY_INDEX = "daily_index"
    JSON_METADATA = "json_metadata"
    FILING_DOCUMENT = "filing_document"


class SecSecurityReason(StrEnum):
    """Stable, payload-free reason codes for security boundary failures."""

    __slots__ = ()

    HOST = "host"
    REDIRECT = "redirect"
    CONTENT_TYPE = "content_type"
    RESPONSE_SIZE = "response_size"
    CACHE_PATH = "cache_path"
    XML = "xml"
    ZIP = "zip"


@dataclass(frozen=True, slots=True)
class SecResourceLimits:
    """Allowed media types and byte ceiling for one SEC resource profile."""

    allowed_media_types: frozenset[str]
    max_bytes: int

    def __post_init__(self) -> None:
        media_types = _freeze_media_types(self.allowed_media_types)
        _validate_positive_int("max_bytes", self.max_bytes)
        object.__setattr__(self, "allowed_media_types", media_types)


@dataclass(frozen=True, slots=True)
class SecSecurityPolicy:
    """Validated, immutable limits shared by SEC ingestion boundaries."""

    allowed_hosts: frozenset[str]
    max_redirects: int
    request_timeout_seconds: tuple[float, float]
    response_chunk_bytes: int
    cache_freshness_seconds: float
    xml_max_bytes: int
    xml_max_elements: int
    xml_max_depth: int
    xml_max_text_bytes: int
    xml_max_scalar_chars: int
    xml_max_long_text_chars: int
    xml_max_numeric_chars: int
    zip_max_member_name_chars: int
    zip_max_entries: int
    zip_max_member_bytes: int
    zip_max_total_bytes: int
    zip_max_compression_ratio: float
    resource_limits: Mapping[SecResourceProfile, SecResourceLimits]

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_hosts", _freeze_hosts(self.allowed_hosts))
        object.__setattr__(
            self,
            "request_timeout_seconds",
            _validate_timeout_tuple(self.request_timeout_seconds),
        )
        object.__setattr__(
            self,
            "zip_max_compression_ratio",
            _validate_ratio(self.zip_max_compression_ratio),
        )
        object.__setattr__(
            self,
            "resource_limits",
            _freeze_resource_limits(self.resource_limits),
        )
        _validate_policy_numbers(self)

    def limits_for(self, profile: SecResourceProfile) -> SecResourceLimits:
        """Return immutable limits for an SEC resource profile."""
        return self.resource_limits[profile]


def _freeze_hosts(hosts: object) -> frozenset[str]:
    values = _collect_strings(hosts, "SEC host collection")
    if not values or any(
        host != host.lower()
        or not host.isascii()
        or _HOST_PATTERN.fullmatch(host) is None
        for host in values
    ):
        raise ValueError("SEC hosts must be exact lowercase ASCII hostnames")
    return frozenset(values)


def _freeze_media_types(media_types: object) -> frozenset[str]:
    values = _collect_strings(media_types, "media type collection")
    if not values or any(
        media_type != media_type.lower()
        or not media_type.isascii()
        or _MEDIA_TYPE_PATTERN.fullmatch(media_type) is None
        for media_type in values
    ):
        raise ValueError("media types must be normalized lowercase values")
    return frozenset(values)


def _collect_strings(values: object, label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise ValueError(f"{label} must be a nonempty collection")
    collected = tuple(values)
    if not collected or any(not isinstance(value, str) for value in collected):
        raise ValueError(f"{label} must contain only strings")
    return collected


def _validate_positive_int(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _validate_positive_number(name: str, value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be finite and greater than zero")
    return float(value)


def _validate_timeout_tuple(timeout: object) -> tuple[float, float]:
    if not isinstance(timeout, tuple) or len(timeout) != 2:
        raise ValueError("request timeout must be a connect/read tuple")
    connect = _validate_positive_number("connect timeout", timeout[0])
    read = _validate_positive_number("read timeout", timeout[1])
    return (connect, read)


def _validate_ratio(value: object) -> float:
    ratio = _validate_positive_number("ZIP compression ratio", value)
    if ratio <= 1:
        raise ValueError("ZIP compression ratio must be greater than one")
    return ratio


def _validate_policy_numbers(policy: SecSecurityPolicy) -> None:
    integer_limits = (
        ("max_redirects", policy.max_redirects),
        ("response_chunk_bytes", policy.response_chunk_bytes),
        ("xml_max_bytes", policy.xml_max_bytes),
        ("xml_max_elements", policy.xml_max_elements),
        ("xml_max_depth", policy.xml_max_depth),
        ("xml_max_text_bytes", policy.xml_max_text_bytes),
        ("xml_max_scalar_chars", policy.xml_max_scalar_chars),
        ("xml_max_long_text_chars", policy.xml_max_long_text_chars),
        ("xml_max_numeric_chars", policy.xml_max_numeric_chars),
        ("zip_max_member_name_chars", policy.zip_max_member_name_chars),
        ("zip_max_entries", policy.zip_max_entries),
        ("zip_max_member_bytes", policy.zip_max_member_bytes),
        ("zip_max_total_bytes", policy.zip_max_total_bytes),
    )
    for name, value in integer_limits:
        _validate_positive_int(name, value)
    _validate_positive_number("cache_freshness_seconds", policy.cache_freshness_seconds)


def _freeze_resource_limits(
    limits: object,
) -> Mapping[SecResourceProfile, SecResourceLimits]:
    if not isinstance(limits, Mapping):
        raise ValueError("resource profile limits must be a mapping")
    copied = dict(limits)
    if set(copied) != set(SecResourceProfile) or any(
        not isinstance(value, SecResourceLimits) for value in copied.values()
    ):
        raise ValueError("resource profile limits must cover every profile exactly")
    return MappingProxyType(copied)


DEFAULT_SEC_SECURITY_POLICY = SecSecurityPolicy(
    allowed_hosts=frozenset({"www.sec.gov", "data.sec.gov"}),
    max_redirects=3,
    request_timeout_seconds=(15.0, 15.0),
    response_chunk_bytes=64 * 1024,
    cache_freshness_seconds=24 * 60 * 60,
    xml_max_bytes=8 * _MIB,
    xml_max_elements=100_000,
    xml_max_depth=64,
    xml_max_text_bytes=2 * _MIB,
    xml_max_scalar_chars=4_096,
    xml_max_long_text_chars=256 * 1024,
    xml_max_numeric_chars=128,
    zip_max_member_name_chars=512,
    zip_max_entries=2_000_000,
    zip_max_member_bytes=64 * _MIB,
    zip_max_total_bytes=16 * 1024 * _MIB,
    zip_max_compression_ratio=200.0,
    resource_limits={
        SecResourceProfile.DAILY_INDEX: SecResourceLimits(
            # SEC serves daily/full-index .idx files as application/octet-stream;
            # text/plain is also accepted for resilience to header variation.
            frozenset({"text/plain", "application/octet-stream"}),
            32 * _MIB,
        ),
        SecResourceProfile.JSON_METADATA: SecResourceLimits(
            frozenset({"application/json", "text/plain"}), 32 * _MIB
        ),
        SecResourceProfile.FILING_DOCUMENT: SecResourceLimits(
            frozenset(
                {
                    "text/plain",
                    "application/xml",
                    "text/xml",
                    "application/octet-stream",
                }
            ),
            32 * _MIB,
        ),
    },
)
