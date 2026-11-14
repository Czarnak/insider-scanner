"""Rate-limited HTTP client with SEC EDGAR compliance and optional caching."""

from __future__ import annotations

import time
from pathlib import Path

import requests

from insider_scanner.utils.caching import cache_key, get_cached, set_cached
from insider_scanner.utils.config import SEC_MAX_REQUESTS_PER_SECOND, SEC_USER_AGENT
from insider_scanner.utils.logging import get_logger

log = get_logger("http")

# Module-level rate limiter
_last_request_time: float = 0.0
_min_interval: float = 1.0 / SEC_MAX_REQUESTS_PER_SECOND


def _rate_limit() -> None:
    """Block until enough time has passed since the last request."""
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _min_interval:
        time.sleep(_min_interval - elapsed)
    _last_request_time = time.time()


def fetch_url(
    url: str,
    *,
    cache_dir: Path | None = None,
    cache_ttl: int = 3600,
    headers: dict | None = None,
    timeout: int = 15,
    use_sec_agent: bool = False,
) -> str:
    """Fetch a URL with optional caching and rate limiting.

    Parameters
    ----------
    url : str
        URL to fetch.
    cache_dir : Path or None
        If provided, cache responses here.
    cache_ttl : int
        Cache time-to-live in seconds.
    headers : dict or None
        Additional HTTP headers.
    timeout : int
        Request timeout in seconds.
    use_sec_agent : bool
        If True, use SEC-compliant User-Agent and rate limiting.

    Returns
    -------
    str
        Response body text.

    Raises
    ------
    requests.HTTPError
        On non-2xx responses.
    """
    # Check cache first
    if cache_dir is not None:
        key = cache_key(url)
        cached = get_cached(cache_dir, key, cache_ttl)
        if cached is not None:
            log.debug("Cache hit for %s", url)
            return cached

    # Build headers
    req_headers = dict(headers or {})
    if use_sec_agent:
        req_headers["User-Agent"] = SEC_USER_AGENT
        _rate_limit()
    else:
        if "User-Agent" not in req_headers:
            req_headers["User-Agent"] = "InsiderScanner/0.1"

    log.debug("Fetching %s", url)
    resp = requests.get(url, headers=req_headers, timeout=timeout)
    resp.raise_for_status()
    text = resp.text

    # Store in cache
    if cache_dir is not None:
        key = cache_key(url)
        set_cached(cache_dir, key, text, ttl=cache_ttl)

    return text
