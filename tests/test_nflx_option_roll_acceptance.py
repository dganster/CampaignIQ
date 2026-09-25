from datetime import date
from decimal import Decimal
from pathlib import Path

from campaigniq.domain.option_roll import detect_option_rolls
from campaigniq.importers.thinkorswim.trade_history_reader import (
    ThinkorswimTradeHistoryReader,
)
from campaigniq.importers.thinkorswim.translator import to_trade
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader


DATA = Path("tests/data")
MARCH = DATA / "thinkorswim/Account Trade History March 2026.csv"


def _march_trades():
    statement = ThinkorswimSourceReader().read(MARCH)
    section = statement.section("Account Trade History")
    orders = ThinkorswimTradeHistoryReader().read(section)

    return [
        to_trade(order)
        for order in orders
        if not any(
            row.option_type.upper() == "FOREX"
            for row in order.legs
        )
    ]


def _nflx_rolls():
    result = []

    for trade in _march_trades():
        for roll in detect_option_rolls(trade):
            if roll.underlying == "NFLX":
                result.append(roll)

    return result


def test_real_march_history_contains_two_nflx_rolls() -> None:
    rolls = _nflx_rolls()

    assert len(rolls) == 2

    transitions = {
        (
            roll.closed_contract.expiration,
            roll.opened_contract.expiration,
            roll.closed_contract.strike,
            roll.opened_contract.strike,
            roll.quantity,
        )
        for roll in rolls
    }

    assert transitions == {
        (
            date(2026, 3, 20),
            date(2026, 4, 17),
            Decimal("74"),
            Decimal("74"),
            Decimal("50"),
        ),
        (
            date(2026, 4, 17),
            date(2026, 5, 15),
            Decimal("74"),
            Decimal("74"),
            Decimal("50"),
        ),
    }


def test_real_nflx_rolls_preserve_short_call_direction() -> None:
    rolls = _nflx_rolls()

    assert len(rolls) == 2

    for roll in rolls:
        assert roll.closed_leg.quantity == Decimal("50")
        assert roll.opened_leg.quantity == Decimal("-50")


def test_real_nflx_rolls_are_expiration_rolls_not_strike_rolls() -> None:
    rolls = _nflx_rolls()

    assert len(rolls) == 2
    assert all(roll.expiration_changed for roll in rolls)
    assert all(not roll.strike_changed for roll in rolls)
