from datetime import date, datetime
from decimal import Decimal

import pytest

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.importers.schwab.option_assignment import SchwabOptionAssignment
from campaigniq.importers.schwab.translator import to_position_event


@pytest.mark.parametrize(
    ("symbol", "strike", "quantity"),
    (
        ("APD", "270", "1"),
        ("AXP", "300", "1"),
        ("JPM", "280", "1"),
        ("CVX", "162.50", "1"),
        ("DE", "570", "1"),
        ("DELL", "390", "1"),
        ("LLY", "1050", "1"),
        ("GS", "1035", "1"),
        ("HD", "310", "1"),
        ("LMT", "480", "1"),
        ("META", "530", "5"),
        ("PNC", "242.50", "1"),
        ("UNH", "382.50", "1"),
        ("WMT", "107", "1"),
    ),
)
def test_july_2026_assignment_closes_only_assigned_option(
    symbol: str,
    strike: str,
    quantity: str,
) -> None:
    assignment = SchwabOptionAssignment(
        occurred_at=datetime(2026, 7, 20, 0, 0),
        symbol=symbol,
        expiration=date(2026, 7, 17),
        strike=Decimal(strike),
        option_type="CALL",
        quantity=Decimal(quantity),
    )

    event = to_position_event(assignment)

    expected_contract = OptionContract(
        underlying=symbol,
        expiration=date(2026, 7, 17),
        strike=Decimal(strike),
        option_type=OptionType.CALL,
    )

    assert event.kind == PositionEventKind.ASSIGNMENT
    assert event.occurred_at == datetime(2026, 7, 20, 0, 0)
    assert len(event.changes) == 1
    assert event.changes[0].instrument == expected_contract
    assert event.changes[0].quantity == Decimal(quantity)
