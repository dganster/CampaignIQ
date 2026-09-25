from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.historical_lot_reconstructor import (
    HistoricalLotReconstructor,
)
from campaigniq.domain.leg import Leg
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade


SNAPSHOT_AT = datetime(2026, 2, 28, 23, 59, 59)


def contract(expiration: date) -> OptionContract:
    return OptionContract(
        underlying="NFLX",
        expiration=expiration,
        strike=Decimal("74"),
        option_type=OptionType.CALL,
    )


def leg(
    instrument: OptionContract,
    *,
    side: Side,
    effect: PositionEffect,
    quantity: str,
    when: datetime,
) -> Leg:
    return Leg(
        instrument=instrument,
        side=side,
        position_effect=effect,
        executions=(
            Execution(
                quantity=Decimal(quantity),
                execution_price=Decimal("1"),
                executed_at=when,
            ),
        ),
    )


def test_advances_snapshot_through_two_historical_rolls() -> None:
    march_call = contract(date(2026, 3, 20))
    april_call = contract(date(2026, 4, 17))
    may_call = contract(date(2026, 5, 15))

    book = LotBook()
    book.seed(
        Lot(
            lot_id="SNAPSHOT:NFLX-MAR20",
            instrument=march_call,
            quantity=Decimal("-50"),
            opened_at=SNAPSHOT_AT,
            basis_total=Decimal("-39066.79"),
            basis_source="SCHWAB_POSITION_SNAPSHOT",
        )
    )

    first_roll = Trade(
        legs=(
            leg(
                april_call,
                side=Side.SELL,
                effect=PositionEffect.OPEN,
                quantity="-50",
                when=datetime(2026, 3, 10, 12, 2, 32),
            ),
            leg(
                march_call,
                side=Side.BUY,
                effect=PositionEffect.CLOSE,
                quantity="50",
                when=datetime(2026, 3, 10, 12, 2, 32),
            ),
        )
    )

    second_roll = Trade(
        legs=(
            leg(
                may_call,
                side=Side.SELL,
                effect=PositionEffect.OPEN,
                quantity="-50",
                when=datetime(2026, 3, 27, 11, 53, 38),
            ),
            leg(
                april_call,
                side=Side.BUY,
                effect=PositionEffect.CLOSE,
                quantity="50",
                when=datetime(2026, 3, 27, 11, 53, 38),
            ),
        )
    )

    advanced = (
        HistoricalLotReconstructor
        .advance_snapshot_through_historical_trades(
            book,
            (second_roll, first_roll),
            snapshot_at=SNAPSHOT_AT,
        )
    )

    assert advanced.lots(march_call) == ()
    assert advanced.lots(april_call) == ()

    may_lots = advanced.lots(may_call)
    assert len(may_lots) == 1
    assert may_lots[0].quantity == Decimal("-50")

    # Source snapshot remains authoritative and unchanged.
    assert len(book.lots(march_call)) == 1
    assert book.lots(march_call)[0].quantity == Decimal("-50")


def test_trade_at_snapshot_time_is_not_replayed() -> None:
    march_call = contract(date(2026, 3, 20))

    book = LotBook()
    book.seed(
        Lot(
            lot_id="SNAPSHOT:NFLX-MAR20",
            instrument=march_call,
            quantity=Decimal("-50"),
            opened_at=SNAPSHOT_AT,
            basis_total=None,
            basis_source="SCHWAB_POSITION_SNAPSHOT",
        )
    )

    already_reflected = Trade(
        legs=(
            leg(
                march_call,
                side=Side.BUY,
                effect=PositionEffect.CLOSE,
                quantity="50",
                when=SNAPSHOT_AT,
            ),
        )
    )

    advanced = (
        HistoricalLotReconstructor
        .advance_snapshot_through_historical_trades(
            book,
            (already_reflected,),
            snapshot_at=SNAPSHOT_AT,
        )
    )

    assert advanced.lots(march_call) == book.lots(march_call)


def test_trade_before_snapshot_is_not_replayed() -> None:
    march_call = contract(date(2026, 3, 20))

    book = LotBook()
    book.seed(
        Lot(
            lot_id="SNAPSHOT:NFLX-MAR20",
            instrument=march_call,
            quantity=Decimal("-50"),
            opened_at=SNAPSHOT_AT,
            basis_total=None,
            basis_source="SCHWAB_POSITION_SNAPSHOT",
        )
    )

    earlier_close = Trade(
        legs=(
            leg(
                march_call,
                side=Side.BUY,
                effect=PositionEffect.CLOSE,
                quantity="50",
                when=datetime(2026, 2, 27, 12, 0),
            ),
        )
    )

    advanced = (
        HistoricalLotReconstructor
        .advance_snapshot_through_historical_trades(
            book,
            (earlier_close,),
            snapshot_at=SNAPSHOT_AT,
        )
    )

    assert advanced.lots(march_call) == book.lots(march_call)


def test_unanchored_historical_close_is_not_replayed() -> None:
    snapshot_call = contract(date(2026, 3, 20))
    unrelated_call = OptionContract(
        underlying="CMI",
        expiration=date(2026, 3, 20),
        strike=Decimal("540"),
        option_type=OptionType.CALL,
    )

    book = LotBook()
    book.seed(
        Lot(
            lot_id="SNAPSHOT:NFLX-MAR20",
            instrument=snapshot_call,
            quantity=Decimal("-50"),
            opened_at=SNAPSHOT_AT,
            basis_total=None,
            basis_source="SCHWAB_POSITION_SNAPSHOT",
        )
    )

    unrelated_close = Trade(
        legs=(
            leg(
                unrelated_call,
                side=Side.BUY,
                effect=PositionEffect.CLOSE,
                quantity="1",
                when=datetime(2026, 3, 10, 10, 0),
            ),
        )
    )

    advanced = (
        HistoricalLotReconstructor
        .advance_snapshot_through_historical_trades(
            book,
            (unrelated_close,),
            snapshot_at=SNAPSHOT_AT,
        )
    )

    assert advanced.lots(snapshot_call) == book.lots(snapshot_call)
    assert advanced.lots(unrelated_call) == ()


def test_multiple_close_legs_cannot_overconsume_snapshot_inventory() -> None:
    march_call = contract(date(2026, 3, 20))

    book = LotBook()
    book.seed(
        Lot(
            lot_id="SNAPSHOT:NFLX-MAR20",
            instrument=march_call,
            quantity=Decimal("-50"),
            opened_at=SNAPSHOT_AT,
            basis_total=None,
            basis_source="SCHWAB_POSITION_SNAPSHOT",
        )
    )

    over_close = Trade(
        legs=(
            leg(
                march_call,
                side=Side.BUY,
                effect=PositionEffect.CLOSE,
                quantity="30",
                when=datetime(2026, 3, 10, 12, 0),
            ),
            leg(
                march_call,
                side=Side.BUY,
                effect=PositionEffect.CLOSE,
                quantity="30",
                when=datetime(2026, 3, 10, 12, 0),
            ),
        )
    )

    advanced = (
        HistoricalLotReconstructor
        .advance_snapshot_through_historical_trades(
            book,
            (over_close,),
            snapshot_at=SNAPSHOT_AT,
        )
    )

    assert advanced.lots(march_call) == book.lots(march_call)
