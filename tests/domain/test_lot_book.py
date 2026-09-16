from datetime import datetime
from decimal import Decimal

import pytest

from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.leg import Leg
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument


def stock_trade(symbol: str, side: Side, effect: PositionEffect, qty: str, price: str, day: int) -> Trade:
    return Trade(legs=(InstrumentLeg(
        instrument=Instrument(symbol),
        side=side,
        position_effect=effect,
        executions=(Execution(Decimal(qty), Decimal(price), datetime(2026, 1, day, 10, 0)),),
    ),))


def test_fifo_allocates_partial_close_to_oldest_lot() -> None:
    book = LotBook()
    book.apply_trade(stock_trade("IBM", Side.BUY, PositionEffect.OPEN, "100", "100", 2))
    book.apply_trade(stock_trade("IBM", Side.BUY, PositionEffect.OPEN, "50", "110", 3))

    allocations = book.apply_trade(
        stock_trade("IBM", Side.SELL, PositionEffect.CLOSE, "120", "120", 4)
    )

    assert [a.quantity for a in allocations] == [Decimal("100"), Decimal("20")]
    assert len(book.lots(Instrument("IBM"))) == 1
    assert book.lots(Instrument("IBM"))[0].quantity == Decimal("30")


def test_seeded_preperiod_lot_can_be_closed() -> None:
    book = LotBook()
    book.seed(Lot("DEC-IBM", Instrument("IBM"), Decimal("100"), datetime(2025, 12, 31, 16), Decimal("9000"), "DECEMBER_SNAPSHOT"))
    allocations = book.apply_trade(stock_trade("IBM", Side.SELL, PositionEffect.CLOSE, "100", "100", 5))
    assert allocations[0].lot_id == "DEC-IBM"
    assert book.lots(Instrument("IBM")) == ()


def test_short_lots_close_with_buy() -> None:
    book = LotBook()
    book.apply_trade(stock_trade("IBM", Side.SELL, PositionEffect.OPEN, "100", "100", 2))
    allocations = book.apply_trade(stock_trade("IBM", Side.BUY, PositionEffect.CLOSE, "40", "90", 3))
    assert allocations[0].quantity == Decimal("40")
    assert book.lots(Instrument("IBM"))[0].quantity == Decimal("-60")

def test_partial_close_reduces_seeded_long_lot_basis_proportionally() -> None:
    book = LotBook()
    book.seed(
        Lot(
            "DEC-IBM",
            Instrument("IBM"),
            Decimal("100"),
            datetime(2025, 12, 31, 16),
            Decimal("10000"),
            "DECEMBER_SNAPSHOT",
        )
    )

    book.apply_trade(
        stock_trade("IBM", Side.SELL, PositionEffect.CLOSE, "40", "120", 5)
    )

    remaining = book.lots(Instrument("IBM"))
    assert len(remaining) == 1
    assert remaining[0].quantity == Decimal("60")
    assert remaining[0].basis_total == Decimal("6000")
    assert remaining[0].basis_source == "DECEMBER_SNAPSHOT"


def test_partial_close_reduces_seeded_short_lot_basis_proportionally() -> None:
    book = LotBook()
    book.seed(
        Lot(
            "DEC-IBM-SHORT",
            Instrument("IBM"),
            Decimal("-100"),
            datetime(2025, 12, 31, 16),
            Decimal("-10000"),
            "DECEMBER_SNAPSHOT",
        )
    )

    book.apply_trade(
        stock_trade("IBM", Side.BUY, PositionEffect.CLOSE, "40", "90", 5)
    )

    remaining = book.lots(Instrument("IBM"))
    assert len(remaining) == 1
    assert remaining[0].quantity == Decimal("-60")
    assert remaining[0].basis_total == Decimal("-6000")
    assert remaining[0].basis_source == "DECEMBER_SNAPSHOT"


def test_clone_is_independent_and_preserves_generated_lot_sequence() -> None:
    book = LotBook()
    book.apply_trade(
        stock_trade("IBM", Side.BUY, PositionEffect.OPEN, "100", "100", 2)
    )

    cloned = book.clone()
    cloned.apply_trade(
        stock_trade("IBM", Side.BUY, PositionEffect.OPEN, "50", "110", 3)
    )

    original_lots = book.lots(Instrument("IBM"))
    cloned_lots = cloned.lots(Instrument("IBM"))

    assert len(original_lots) == 1
    assert original_lots[0].lot_id == "LOT-000001"

    assert len(cloned_lots) == 2
    assert [lot.lot_id for lot in cloned_lots] == [
        "LOT-000001",
        "LOT-000002",
    ]

