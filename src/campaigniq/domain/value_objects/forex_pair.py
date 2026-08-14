"""Forex currency-pair value object."""

from dataclasses import dataclass

from campaigniq.domain.value_objects.instrument import Instrument


@dataclass(frozen=True, slots=True)
class ForexPair(Instrument):
    """A spot-forex currency pair."""

    base_currency: str
    quote_currency: str

    def __init__(self, base_currency: str, quote_currency: str) -> None:
        base = base_currency.upper()
        quote = quote_currency.upper()

        if len(base) != 3 or not base.isalpha():
            raise ValueError(f"Invalid base currency: {base_currency!r}")

        if len(quote) != 3 or not quote.isalpha():
            raise ValueError(f"Invalid quote currency: {quote_currency!r}")

        if base == quote:
            raise ValueError("A forex pair cannot contain the same currency twice")

        object.__setattr__(self, "symbol", f"{base}/{quote}")
        object.__setattr__(self, "base_currency", base)
        object.__setattr__(self, "quote_currency", quote)

    @classmethod
    def from_symbol(cls, symbol: str) -> "ForexPair":
        """Construct a forex pair from a BASE/QUOTE symbol."""
        parts = symbol.strip().upper().split("/")

        if len(parts) != 2:
            raise ValueError(f"Invalid forex pair: {symbol!r}")

        return cls(parts[0], parts[1])
