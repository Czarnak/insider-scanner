from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

from insider_scanner import cli
from insider_scanner.core.prices.model import PriceBar


def test_cmd_price_prints_bars(capsys, monkeypatch):
    def fake_history(symbol, start, end, **kw):
        return [
            PriceBar(symbol, date(2026, 1, 5), 1, 2, 0.5, 1.5, 100),
            PriceBar(symbol, date(2026, 1, 6), 1.5, 2, 1, 1.8, 120),
        ]

    history = Mock(side_effect=fake_history)
    services = SimpleNamespace(prices=SimpleNamespace(get_history=history))
    parser = cli.build_parser()
    args = parser.parse_args(
        ["price", "AAPL", "--since", "2026-01-01", "--until", "2026-01-31"]
    )
    args.func(args, services)
    out = capsys.readouterr().out
    assert "AAPL" in out
    assert "2026-01-05" in out
    assert "1.5" in out
    history.assert_called_once_with("AAPL", date(2026, 1, 1), date(2026, 1, 31))
