from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_exit import detect_position_exit
from campaigniq.domain.side import Side
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.import_pipeline import PeriodImportPipeline


DATA = Path("tests/data")

APRIL = DATA / "thinkorswim/Account Trade History April 2026.csv"
MARCH = DATA / "thinkorswim/Account Trade History March 2026.csv"
MARCH_POSITIONS = DATA / "schwab/march_positions.txt"
APRIL_REALIZED = DATA / "schwab/april_realized_gain_loss.txt"


def _underlying(instrument) -> str:
    if isinstance(instrument, OptionContract):
        return instrument.underlying
    return instrument.symbol


def _april_result():
    return PeriodImportPipeline().run(
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        thinkorswim_trade_history=APRIL,
        opening_snapshot=MARCH_POSITIONS,
        opening_snapshot_at=datetime(2026, 2, 28, 23, 59, 59),
        realized_gain_loss_report=APRIL_REALIZED,
        historical_trade_histories=(MARCH,),
        historical_period_start=date(2026, 3, 1),
    )


def _nflx_quantity(book, instrument) -> Decimal:
    return sum(
        (lot.quantity for lot in book.lots(instrument)),
        Decimal("0"),
    )


def _nflx_opening_positions(book):
    return {
        instrument: _nflx_quantity(book, instrument)
        for instrument in book.instruments()
        if _underlying(instrument) == "NFLX"
    }


def _nflx_april_trade(result):
    matches = []

    for trade in result.trades:
        nflx_legs = [
            leg
            for leg in trade.legs
            if _underlying(leg.instrument) == "NFLX"
        ]

        if nflx_legs:
            matches.append(trade)

    assert len(matches) == 1
    return matches[0]


def test_authoritative_april_opening_state_contains_nflx_covered_position() -> None:
    result = _april_result()

    assert result.boundary_reconstruction.unresolved_positions == ()
    assert result.boundary_reconstruction.unresolved_campaigns == ()
    assert result.boundary_reconstruction.historical_requirements == ()

    positions = _nflx_opening_positions(result.opening_lot_book)

    stock_positions = {
        instrument: quantity
        for instrument, quantity in positions.items()
        if isinstance(instrument, Instrument)
    }
    option_positions = {
        instrument: quantity
        for instrument, quantity in positions.items()
        if isinstance(instrument, OptionContract)
    }

    assert stock_positions == {
        Instrument("NFLX"): Decimal("5000"),
    }

    assert len(option_positions) == 1

    contract, quantity = next(iter(option_positions.items()))

    assert contract.underlying == "NFLX"
    assert contract.expiration == date(2026, 5, 15)
    assert contract.strike == Decimal("74")
    assert quantity == Decimal("-50")


def test_real_april_nflx_trade_closes_stock_and_call() -> None:
    result = _april_result()
    trade = _nflx_april_trade(result)

    assert len(trade.legs) == 2

    stock_legs = [
        leg
        for leg in trade.legs
        if isinstance(leg.instrument, Instrument)
    ]
    option_legs = [
        leg
        for leg in trade.legs
        if isinstance(leg.instrument, OptionContract)
    ]

    assert len(stock_legs) == 1
    assert len(option_legs) == 1

    stock_leg = stock_legs[0]
    option_leg = option_legs[0]

    assert stock_leg.side is Side.SELL
    assert stock_leg.position_effect is PositionEffect.CLOSE
    assert stock_leg.quantity == Decimal("-5000")

    assert option_leg.side is Side.BUY
    assert option_leg.position_effect is PositionEffect.CLOSE
    assert option_leg.quantity == Decimal("50")
    assert option_leg.contract.expiration == date(2026, 5, 15)
    assert option_leg.contract.strike == Decimal("74")
    assert option_leg.broker_strategy == "COVERED"


def test_real_april_nflx_trade_is_detected_as_complete_exit() -> None:
    result = _april_result()
    trade = _nflx_april_trade(result)

    exits = detect_position_exit(
        opening_lot_book=result.opening_lot_book,
        trade=trade,
    )

    nflx_exits = [
        exit_
        for exit_ in exits
        if exit_.underlying == "NFLX"
    ]

    assert len(nflx_exits) == 1

    exit_ = nflx_exits[0]

    assert exit_.trade is trade
    assert exit_.after_positions == ()

    before = dict(exit_.before_positions)

    assert before[Instrument("NFLX")] == Decimal("5000")

    option_positions = {
        instrument: quantity
        for instrument, quantity in before.items()
        if isinstance(instrument, OptionContract)
    }

    assert len(option_positions) == 1

    contract, quantity = next(iter(option_positions.items()))

    assert contract.expiration == date(2026, 5, 15)
    assert contract.strike == Decimal("74")
    assert quantity == Decimal("-50")
