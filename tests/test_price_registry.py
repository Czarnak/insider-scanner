"""Tests for price provider registry and selection."""

import pytest

from insider_scanner.core.prices.registry import get_price_source
from insider_scanner.core.prices.tiingo import TiingoSource
from insider_scanner.core.prices.yahoo import YahooSource

def test_registry_defaults_to_yahoo(monkeypatch):
    monkeypatch.delenv("PRICE_SOURCE", raising=False)
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    
    source = get_price_source()
    assert isinstance(source, YahooSource)

def test_registry_defaults_to_tiingo_when_key_present(monkeypatch):
    monkeypatch.delenv("PRICE_SOURCE", raising=False)
    monkeypatch.setenv("TIINGO_API_KEY", "testkey")
    
    source = get_price_source()
    assert isinstance(source, TiingoSource)

def test_explicit_yahoo_ignores_key(monkeypatch):
    monkeypatch.setenv("TIINGO_API_KEY", "testkey")
    source = get_price_source(name="yahoo")
    assert isinstance(source, YahooSource)
    
    monkeypatch.setenv("PRICE_SOURCE", "yahoo")
    source2 = get_price_source()
    assert isinstance(source2, YahooSource)

def test_explicit_tiingo_requires_key(monkeypatch):
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    
    with pytest.raises(ValueError, match="required"):
        get_price_source(name="tiingo")
        
    monkeypatch.setenv("PRICE_SOURCE", "tiingo")
    with pytest.raises(ValueError, match="required"):
        get_price_source()

def test_explicit_tiingo_with_key_succeeds(monkeypatch):
    monkeypatch.setenv("TIINGO_API_KEY", "envkey")
    source = get_price_source(name="tiingo")
    assert isinstance(source, TiingoSource)
    
    source2 = get_price_source(name="tiingo", api_key="argkey")
    assert isinstance(source2, TiingoSource)

def test_unknown_provider_fails(monkeypatch):
    with pytest.raises(ValueError, match="Unknown price provider"):
        get_price_source(name="unknown")
        
    monkeypatch.setenv("PRICE_SOURCE", "invalid")
    with pytest.raises(ValueError, match="Unknown price provider"):
        get_price_source()

def test_normalize_provider_name():
    source = get_price_source(name="  YaHoO  ")
    assert isinstance(source, YahooSource)
    
    source2 = get_price_source(name="TIINGO", api_key="key")
    assert isinstance(source2, TiingoSource)

def test_blank_environment_treated_as_absent(monkeypatch):
    monkeypatch.setenv("PRICE_SOURCE", "   ")
    monkeypatch.setenv("TIINGO_API_KEY", "   ")
    
    source = get_price_source()
    assert isinstance(source, YahooSource)

def test_arguments_override_environment(monkeypatch):
    monkeypatch.setenv("PRICE_SOURCE", "tiingo")
    monkeypatch.setenv("TIINGO_API_KEY", "envkey")
    
    source = get_price_source(name="yahoo")
    assert isinstance(source, YahooSource)
    
    source2 = get_price_source(name="yahoo", api_key="  ")
    assert isinstance(source2, YahooSource)

def test_key_leakage_in_exceptions(monkeypatch):
    monkeypatch.setenv("TIINGO_API_KEY", "secret_key_123")
    try:
        get_price_source(name="unknown")
    except ValueError as e:
        assert "secret_key_123" not in str(e)
        assert "secret_key_123" not in repr(e)
