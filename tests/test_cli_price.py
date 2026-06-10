from datetime import date

from insider_scanner.core.prices.model import PriceBar
from insider_scanner import cli

def test_cmd_price_prints_bars(capsys, monkeypatch):
    def fake_history(symbol, start, end, **kw):
        return [
            PriceBar(symbol, date(2026, 1, 5), 1, 2, 0.5, 1.5, 100),
            PriceBar(symbol, date(2026, 1, 6), 1.5, 2, 1, 1.8, 120),
        ]

    monkeypatch.setattr(cli, "get_price_history", fake_history, raising=False)
    parser = cli.build_parser()
    args = parser.parse_args(["price", "AAPL", "--since", "2026-01-01", "--until", "2026-01-31"])
    args.func(args, None)
    out = capsys.readouterr().out
    assert "AAPL" in out
    assert "2026-01-05" in out
    assert "1.5" in out
