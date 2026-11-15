"""Discover published SEC Form 3/4/5 quarters and download bounded ZIP files."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from insider_scanner.core.sec_client import SecClient, SecClientError
from insider_scanner.core.sec_security import SecResourceProfile

SEC_DATASET_INDEX_URL = (
    "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
)
_QUARTER_FILENAME = re.compile(r"([0-9]{4})q([1-4])_form345\.zip\Z", re.ASCII)


class SecDatasetDiscoveryError(SecClientError):
    """The SEC index did not advertise any trustworthy quarterly archives."""


@dataclass(frozen=True, slots=True)
class DatasetQuarter:
    """A published quarter identified by its advertised SEC ZIP URL."""

    year: int
    quarter: int
    url: str

    def __post_init__(self) -> None:
        if (
            type(self.year) is not int
            or type(self.quarter) is not int
            or _quarter_identity(self.url) != (self.year, self.quarter)
        ):
            raise ValueError("Dataset quarter must match an approved SEC archive URL")

    @property
    def filename(self) -> str:
        return f"{self.year}q{self.quarter}_form345.zip"


class _DatasetLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.quarters: dict[tuple[int, int], DatasetQuarter] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if not href or any(char.isspace() for char in href):
            return
        # Do not normalize dot segments before checking them: urljoin would
        # otherwise hide a traversal in an advertised path.
        try:
            path = urlsplit(href).path
            url = urljoin(SEC_DATASET_INDEX_URL, href)
        except ValueError:
            return
        if any(part == ".." for part in path.split("/")):
            return
        identity = _quarter_identity(url)
        if identity is not None:
            self.quarters.setdefault(identity, DatasetQuarter(*identity, url))


def _quarter_identity(url: str) -> tuple[int, int] | None:
    if not isinstance(url, str) or any(char.isspace() for char in url):
        return None
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "www.sec.gov"
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/files/")
            or "%" in parsed.path
            or "\\" in parsed.path
            or any(part in {".", ".."} for part in parsed.path.split("/"))
        ):
            return None
    except ValueError:
        return None
    match = _QUARTER_FILENAME.fullmatch(parsed.path.rsplit("/", 1)[-1])
    if match is None:
        return None
    year, quarter = (int(part) for part in match.groups())
    return (year, quarter) if year >= 2006 else None


def discover_dataset_quarters(client: SecClient) -> tuple[DatasetQuarter, ...]:
    """Read actual published links; an absent quarter is never guessed."""
    page = client.fetch_text(
        SEC_DATASET_INDEX_URL, profile=SecResourceProfile.DATASET_INDEX
    )
    parser = _DatasetLinks()
    parser.feed(page)
    parser.close()
    if not parser.quarters:
        raise SecDatasetDiscoveryError(
            "SEC index has no published quarterly dataset links"
        )
    return tuple(parser.quarters[key] for key in sorted(parser.quarters))


def download_dataset_quarter(
    client: SecClient,
    quarter: DatasetQuarter,
    destination: Path,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    """Download the advertised quarter through SEC fair-access and security rules."""
    return client.fetch_to_file(
        quarter.url,
        destination,
        profile=SecResourceProfile.DATASET_ZIP,
        cancelled=cancelled,
    )
