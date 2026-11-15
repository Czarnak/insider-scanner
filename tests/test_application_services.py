"""Application-owned HTTP resources are always closed during shutdown."""

from datetime import date
from unittest.mock import Mock

import pytest

from insider_scanner.services.application import ApplicationServices


def test_price_service_reuses_lazy_provider_and_application_engine(monkeypatch):
    persistence = Mock()
    source = Mock(name="provider")
    factory = Mock(return_value=source)
    history = Mock(return_value=[])
    monkeypatch.setattr(
        "insider_scanner.core.prices.registry.get_price_source", factory
    )
    monkeypatch.setattr(
        "insider_scanner.core.prices.service.get_price_history", history
    )
    services = ApplicationServices(persistence, Mock(), Mock(), Mock())
    assert hasattr(services, "prices"), "application must own a reusable price service"
    factory.assert_not_called()

    start, end = date(2026, 1, 1), date(2026, 1, 31)
    services.prices.get_history("AAPL", start, end)
    services.prices.get_history("MSFT", start, end)

    factory.assert_called_once_with()
    assert history.call_count == 2
    assert all(
        call.kwargs == {"engine": persistence.engine, "source": source}
        for call in history.call_args_list
    )


def test_shutdown_closes_price_provider_even_if_database_close_fails(monkeypatch):
    persistence = Mock()
    persistence.close.side_effect = RuntimeError("database close failed")
    source = Mock()
    monkeypatch.setattr(
        "insider_scanner.core.prices.registry.get_price_source", lambda: source
    )
    monkeypatch.setattr(
        "insider_scanner.core.prices.service.get_price_history", Mock(return_value=[])
    )
    services = ApplicationServices(persistence, Mock(), Mock(), Mock())
    assert hasattr(services, "prices"), "application must own provider cleanup"
    services.prices.get_history("AAPL", date(2026, 1, 1), date(2026, 1, 2))

    with pytest.raises(RuntimeError, match="database close failed"):
        services.close()

    source.close.assert_called_once_with()


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
