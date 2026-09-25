from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.lot_book_period_applier import LotBookPeriodApplier
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.position_lifecycle_transition import (
    PositionLifecycleTransitionKind,
)
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument


def _execution(
    quantity: str,
    price: str,
    occurred_at: datetime,
) -> Execution:
    return Execution(
        quantity=Decimal(quantity),
        execution_price=Decimal(price),
        executed_at=occurred_at,
    )


def _opening_book() -> LotBook:
    book = LotBook()

    book.seed(
        Lot(
            lot_id="NFLX-STOCK",
            instrument=Instrument("NFLX"),
            quantity=Decimal("5000"),
            opened_at=datetime(2025, 12, 19),
            basis_total=None,
        )
    )

    book.seed(
        Lot(
            lot_id="NFLX-CALL",
            instrument=OptionContract(
                underlying="NFLX",
                expiration=date(2026, 3, 20),
                strike=Decimal("74"),
                option_type=OptionType.CALL,
            ),
            quantity=Decimal("-50"),
            opened_at=datetime(2026, 2, 11),
            basis_total=None,
        )
    )

    return book


def _roll_trade() -> Trade:
    occurred_at = datetime(2026, 3, 10, 12, 2, 32)

    old_contract = OptionContract(
        underlying="NFLX",
        expiration=date(2026, 3, 20),
        strike=Decimal("74"),
        option_type=OptionType.CALL,
    )
    new_contract = OptionContract(
        underlying="NFLX",
        expiration=date(2026, 4, 17),
        strike=Decimal("74"),
        option_type=OptionType.CALL,
    )

    return Trade(
        legs=(
            OptionLeg(
                contract=new_contract,
                side=Side.SELL,
                position_effect=PositionEffect.OPEN,
                executions=(
                    _execution("-50", "23.63", occurred_at),
                ),
                broker_strategy="CALENDAR",
            ),
            OptionLeg(
                contract=old_contract,
                side=Side.BUY,
                position_effect=PositionEffect.CLOSE,
                executions=(
                    _execution("50", "23.23", occurred_at),
                ),
                broker_strategy="CALENDAR",
            ),
        )
    )


def _exit_trade() -> Trade:
    occurred_at = datetime(2026, 4, 16, 10, 52, 1)

    contract = OptionContract(
        underlying="NFLX",
        expiration=date(2026, 4, 17),
        strike=Decimal("74"),
        option_type=OptionType.CALL,
    )

    return Trade(
        legs=(
            OptionLeg(
                contract=contract,
                side=Side.BUY,
                position_effect=PositionEffect.CLOSE,
                executions=(
                    _execution("50", "33.61", occurred_at),
                ),
                broker_strategy="COVERED",
            ),
            InstrumentLeg(
                instrument=Instrument("NFLX"),
                side=Side.SELL,
                position_effect=PositionEffect.CLOSE,
                executions=(
                    _execution("-5000", "107.24", occurred_at),
                ),
            ),
        )
    )


def test_period_replay_collects_roll_then_exit() -> None:
    opening = _opening_book()
    roll = _roll_trade()
    exit_trade = _exit_trade()

    history = PositionHistory()
    history.add_trade(exit_trade)
    history.add_trade(roll)

    campaigns = (
        Campaign(
            campaign_id="CAMP-000001",
            trades=(roll, exit_trade),
        ),
    )

    ending, transitions = (
        LotBookPeriodApplier().apply_with_lifecycle(
            opening_lot_book=opening,
            position_history=history,
            campaigns=campaigns,
        )
    )

    assert [
        transition.kind
        for transition in transitions
    ] == [
        PositionLifecycleTransitionKind.ROLL,
        PositionLifecycleTransitionKind.EXIT,
    ]

    assert transitions[0].option_roll is not None
    assert transitions[1].position_exit is not None

    assert ending.instruments() == ()
    assert opening.instruments() != ()


def test_period_replay_collects_assignment() -> None:
    history = PositionHistory()

    event = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=Instrument("NFLX"),
                quantity=Decimal("5000"),
            ),
        ),
        occurred_at=datetime(2025, 12, 19),
    )
    history.add_event(event)

    ending, transitions = (
        LotBookPeriodApplier().apply_with_lifecycle(
            opening_lot_book=LotBook(),
            position_history=history,
            campaigns=(),
        )
    )

    assert len(transitions) == 1

    transition = transitions[0]
    assert transition.kind is PositionLifecycleTransitionKind.ASSIGNMENT
    assert transition.position_event is event

    assert ending.instruments() == (Instrument("NFLX"),)


def test_plain_apply_preserves_existing_behavior() -> None:
    opening = _opening_book()
    roll = _roll_trade()

    history = PositionHistory()
    history.add_trade(roll)

    campaigns = (
        Campaign(
            campaign_id="CAMP-000001",
            trades=(roll,),
        ),
    )

    applier = LotBookPeriodApplier()

    plain = applier.apply(
        opening_lot_book=opening,
        position_history=history,
        campaigns=campaigns,
    )
    with_lifecycle, transitions = applier.apply_with_lifecycle(
        opening_lot_book=opening,
        position_history=history,
        campaigns=campaigns,
    )

    assert plain.instruments() == with_lifecycle.instruments()

    for instrument in plain.instruments():
        assert plain.lots(instrument) == with_lifecycle.lots(instrument)

    assert len(transitions) == 1
    assert transitions[0].kind is PositionLifecycleTransitionKind.ROLL
