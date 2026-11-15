"""Trusted archive limit overrides retain preflight and readable errors."""

import zipfile

import pytest

from insider_scanner import cli
from insider_scanner.core.sec_security import DEFAULT_SEC_SECURITY_POLICY
from insider_scanner.services.sec_backfill import SecBackfillService
from tests.sec_stubs import make_client, make_transport


def test_archive_limit_override_reports_actual_limit_before_fetch(
    persistence, tmp_path, monkeypatch, capsys
):
    path = tmp_path / "source.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("CIK0000000001.json", "{}")
        archive.writestr("CIK0000000002.json", "{}")
    transport = make_transport()
    monkeypatch.setattr(cli, "_build_sec_client", lambda: make_client(transport))
    monkeypatch.setattr(cli, "_sec_cache_root", lambda: tmp_path / "cache")
    policies = []

    class Services:
        def make_sec_backfill(self, **kwargs):
            policies.append(kwargs["policy"])
            return SecBackfillService(persistence, **kwargs)

    args = cli.build_parser().parse_args(
        [
            "sec-backfill",
            "--zip",
            str(path),
            "--confirm-full-backfill",
            "--zip-max-entries",
            "1",
        ]
    )
    assert cli.cmd_sec_backfill(args, Services()) == 1
    error = capsys.readouterr().err
    assert "zip_max_entries observed=2 allowed=1" in error
    assert "Traceback" not in error
    assert transport.get.call_count == 0
    assert policies[0].zip_max_entries == 1
    assert (
        policies[0].zip_max_member_bytes
        == DEFAULT_SEC_SECURITY_POLICY.zip_max_member_bytes
    )


@pytest.mark.parametrize("limit", ["entries", "total-bytes", "member-bytes"])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_archive_limit_overrides_must_be_positive(limit, value):
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(
            [
                "sec-backfill",
                "--zip",
                "source.zip",
                f"--zip-max-{limit}",
                value,
            ]
        )
    assert error.value.code == 2
