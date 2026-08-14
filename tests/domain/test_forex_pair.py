import pytest

from campaigniq.domain.value_objects.forex_pair import ForexPair
from campaigniq.domain.value_objects.instrument import Instrument


def test_forex_pair_is_an_instrument():
    pair = ForexPair("EUR", "USD")

    assert isinstance(pair, Instrument)
    assert pair.symbol == "EUR/USD"
    assert pair.underlying == "EUR/USD"


def test_forex_pair_normalizes_currency_codes():
    pair = ForexPair("eur", "usd")

    assert pair.base_currency == "EUR"
    assert pair.quote_currency == "USD"
    assert pair.symbol == "EUR/USD"


def test_forex_pair_from_symbol():
    pair = ForexPair.from_symbol("eur/usd")

    assert pair == ForexPair("EUR", "USD")


@pytest.mark.parametrize(
    "symbol",
    [
        "EURUSD",
        "EUR/USD/JPY",
        "EUR/",
        "/USD",
        "",
    ],
)
def test_forex_pair_rejects_invalid_symbols(symbol):
    with pytest.raises(ValueError):
        ForexPair.from_symbol(symbol)


@pytest.mark.parametrize(
    "base,quote",
    [
        ("EU", "USD"),
        ("EURO", "USD"),
        ("EUR", "US"),
        ("EUR", "USDX"),
        ("123", "USD"),
        ("EUR", "123"),
        ("EUR", "EUR"),
    ],
)
def test_forex_pair_rejects_invalid_currencies(base, quote):
    with pytest.raises(ValueError):
        ForexPair(base, quote)
