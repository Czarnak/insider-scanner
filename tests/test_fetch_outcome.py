"""Completeness is independent of whether a source returned usable records."""

import pytest

from insider_scanner.core.fetch_outcome import (
    FetchIssue,
    FetchResult,
    FetchStatus,
    as_fetch_result,
)


def test_empty_success_and_failed_empty_have_different_coverage():
    assert FetchResult().status == FetchStatus.EMPTY
    assert FetchResult().complete
    failed = FetchResult(issues=[FetchIssue("request_failed")])
    assert failed == []
    assert failed.status == FetchStatus.TRANSIENT_FAILURE
    assert not failed.complete


def test_partial_records_retain_identity_and_metadata_through_adapter():
    result = FetchResult([1], issues=[FetchIssue("result_cap", source="example")])
    assert result == [1]
    assert result.status == FetchStatus.PARTIAL
    assert not result.complete
    assert as_fetch_result(result) is result
    assert as_fetch_result([1]).complete


def test_unsupported_is_distinct_from_success_and_retryable_failure():
    result = FetchResult(status=FetchStatus.UNSUPPORTED)
    assert not result.complete
    assert result.status == FetchStatus.UNSUPPORTED


def test_unresolved_issues_cannot_claim_complete_coverage():
    with pytest.raises(ValueError, match="unresolved"):
        FetchResult(status=FetchStatus.COMPLETE, issues=[FetchIssue("missing_page")])
