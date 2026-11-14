"""Application-owned HTTP resources are always closed during shutdown."""

from unittest.mock import Mock

import pytest

from insider_scanner.services.application import ApplicationServices


@pytest.mark.parametrize("failure", [None, RuntimeError("database close failed")])
def test_application_shutdown_closes_http_even_when_persistence_fails(
    monkeypatch, failure
):
    persistence = Mock()
    persistence.close.side_effect = failure
    close_http = Mock()
    monkeypatch.setattr(
        "insider_scanner.services.application.close_http_sessions", close_http
    )
    services = ApplicationServices(persistence, Mock(), Mock(), Mock())

    if failure is None:
        services.close()
    else:
        with pytest.raises(RuntimeError, match="database close failed"):
            services.close()

    close_http.assert_called_once_with()
