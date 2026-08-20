from datetime import date, time
from decimal import Decimal

from campaigniq.domain.position_event import PositionChange
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.thinkorswim.cash_balance_event import (
    to_expiration_event,
)
from campaigniq.importers.thinkorswim.cash_balance_row import (
    ThinkorswimCashBalanceRow,
)


def make_row(
    description: str,
    transaction_type: str = "EXP",
) -> ThinkorswimCashBalanceRow:
    return ThinkorswimCashBalanceRow(
        transaction_date=date(2026, 3, 7),
        transaction_time=time(2, 4, 21),
        transaction_type=transaction_type,
        reference="test",
        description=description,
        misc_fees=None,
        commissions_and_fees=None,
        amount=None,
        balance=None,
    )


def test_exp_sold_creates_negative_stock_change() -> None:
    event = to_expiration_event(
        make_row(
            "SOLD -500.0 UNH UPON UNITEDHEALTH GROUP INC"
        )
    )

    assert event.kind == PositionEventKind.EXPIRATION
    assert event.occurred_at.isoformat() == "2026-03-07T02:04:21"
    assert event.changes == (
        PositionChange(
            instrument=Instrument("UNH"),
            quantity=Decimal("-500"),
        ),
    )


def test_exp_bot_creates_positive_stock_change() -> None:
    event = to_expiration_event(
        make_row(
            "BOT 200.0 SPOT UPON SPOTIFY TECHNOLOGY S A F"
        )
    )

    assert event.changes == (
        PositionChange(
            instrument=Instrument("SPOT"),
            quantity=Decimal("200"),
        ),
    )


def test_exp_rejects_non_exp_transaction() -> None:
    row = make_row(
        "SOLD -500.0 UNH UPON UNITEDHEALTH GROUP INC",
        transaction_type="DOI",
    )

    try:
        to_expiration_event(row)
    except ValueError as exc:
        assert "not an EXP" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
