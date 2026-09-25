from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from campaigniq.domain.covered_position import (
    PositionQuantity,
    detect_covered_call_position,
)
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_roll import detect_option_rolls
from campaigniq.domain.position_exit import detect_position_exit
from campaigniq.domain.position_lifecycle_assembler import (
    ObservedCoveredPosition,
    PositionLifecycleAssembler,
)
from campaigniq.domain.position_lifecycle_transition import (
    PositionLifecycleTransitionKind,
)
from campaigniq.import_pipeline import PeriodImportPipeline
from campaigniq.importers.schwab.position_snapshot import to_lots
from campaigniq.importers.schwab.position_snapshot_reader import (
    read_position_snapshot_section,
)
from campaigniq.importers.thinkorswim.trade_history_reader import (
    ThinkorswimTradeHistoryReader,
)
from campaigniq.importers.thinkorswim.translator import to_trade
from campaigniq.sources.thinkorswim.source_reader import (
    ThinkorswimSourceReader,
)


DATA = Path("tests/data")

DECEMBER_POSITIONS = DATA / "schwab/december_positions.txt"
MARCH = DATA / "thinkorswim/Account Trade History March 2026.csv"
APRIL = DATA / "thinkorswim/Account Trade History April 2026.csv"
MARCH_POSITIONS = DATA / "schwab/march_positions.txt"
APRIL_REALIZED = DATA / "schwab/april_realized_gain_loss.txt"


def _underlying(instrument) -> str:
    if isinstance(instrument, OptionContract):
        return instrument.underlying
    return instrument.symbol


def _december_covered_position():
    observed_at = datetime(2025, 12, 31)

    rows = read_position_snapshot_section(
        DECEMBER_POSITIONS.read_text().splitlines(),
        snapshot_at=observed_at,
    )
    lots = to_lots(list(rows))

    positions = [
        PositionQuantity(
            instrument=lot.instrument,
            quantity=lot.quantity,
        )
        for lot in lots
        if _underlying(lot.instrument) == "NFLX"
    ]

    return ObservedCoveredPosition(
        position=detect_covered_call_position(
            positions,
            underlying="NFLX",
        ),
        observed_at=observed_at,
    )


def _march_nflx_rolls():
    statement = ThinkorswimSourceReader().read(MARCH)
    section = statement.section("Account Trade History")
    orders = ThinkorswimTradeHistoryReader().read(section)

    trades = [
        to_trade(order)
        for order in orders
        if not any(
            row.option_type.upper() == "FOREX"
            for row in order.legs
        )
    ]

    return tuple(
        roll
        for trade in trades
        for roll in detect_option_rolls(trade)
        if roll.underlying == "NFLX"
    )


def _april_result():
    return PeriodImportPipeline().run(
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        thinkorswim_trade_history=APRIL,
        opening_snapshot=MARCH_POSITIONS,
        opening_snapshot_at=datetime(
            2026,
            2,
            28,
            23,
            59,
            59,
        ),
        realized_gain_loss_report=APRIL_REALIZED,
        historical_trade_histories=(MARCH,),
        historical_period_start=date(2026, 3, 1),
    )


def _april_nflx_exit():
    result = _april_result()

    nflx_trades = [
        trade
        for trade in result.trades
        if any(
            _underlying(leg.instrument) == "NFLX"
            for leg in trade.legs
        )
    ]

    assert len(nflx_trades) == 1

    exits = detect_position_exit(
        opening_lot_book=result.opening_lot_book,
        trade=nflx_trades[0],
    )

    nflx_exits = tuple(
        exit_
        for exit_ in exits
        if exit_.underlying == "NFLX"
    )

    assert len(nflx_exits) == 1
    return nflx_exits[0]


def test_real_nflx_evidence_assembles_into_ordered_lifecycle() -> None:
    covered = _december_covered_position()
    rolls = _march_nflx_rolls()
    exit_ = _april_nflx_exit()

    assert len(rolls) == 2

    lifecycle = PositionLifecycleAssembler().assemble(
        symbol="NFLX",
        covered_positions=(covered,),
        option_rolls=rolls,
        position_exits=(exit_,),
    )

    assert [
        transition.kind
        for transition in lifecycle
    ] == [
        PositionLifecycleTransitionKind.COVERED_POSITION,
        PositionLifecycleTransitionKind.ROLL,
        PositionLifecycleTransitionKind.ROLL,
        PositionLifecycleTransitionKind.EXIT,
    ]

    assert [
        transition.occurred_at
        for transition in lifecycle
    ] == [
        datetime(2025, 12, 31),
        datetime(2026, 3, 10, 12, 2, 32),
        datetime(2026, 3, 27, 11, 53, 38),
        datetime(2026, 4, 16, 10, 52, 1),
    ]


def test_real_nflx_lifecycle_preserves_authoritative_evidence() -> None:
    covered = _december_covered_position()
    rolls = _march_nflx_rolls()
    exit_ = _april_nflx_exit()

    lifecycle = PositionLifecycleAssembler().assemble(
        symbol="NFLX",
        covered_positions=(covered,),
        option_rolls=rolls,
        position_exits=(exit_,),
    )

    covered_transition, first_roll, second_roll, exit_transition = lifecycle

    assert covered_transition.covered_position is covered.position

    assert first_roll.option_roll is rolls[0]
    assert second_roll.option_roll is rolls[1]

    assert exit_transition.position_exit is exit_

    assert covered_transition.covered_position.share_quantity == Decimal(
        "5000"
    )
    assert (
        covered_transition.covered_position.short_call_quantity
        == Decimal("50")
    )

    assert first_roll.option_roll.closed_contract.expiration == date(
        2026,
        3,
        20,
    )
    assert first_roll.option_roll.opened_contract.expiration == date(
        2026,
        4,
        17,
    )

    assert second_roll.option_roll.closed_contract.expiration == date(
        2026,
        4,
        17,
    )
    assert second_roll.option_roll.opened_contract.expiration == date(
        2026,
        5,
        15,
    )

    assert exit_transition.position_exit.after_positions == ()


def test_real_nflx_lifecycle_does_not_invent_missing_events() -> None:
    lifecycle = PositionLifecycleAssembler().assemble(
        symbol="NFLX",
        covered_positions=(_december_covered_position(),),
        option_rolls=_march_nflx_rolls(),
        position_exits=(_april_nflx_exit(),),
    )

    kinds = {
        transition.kind
        for transition in lifecycle
    }

    assert PositionLifecycleTransitionKind.ASSIGNMENT not in kinds
    assert PositionLifecycleTransitionKind.CORPORATE_ACTION not in kinds
