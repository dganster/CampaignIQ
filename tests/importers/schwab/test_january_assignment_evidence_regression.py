from datetime import date
from decimal import Decimal
from pathlib import Path

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.schwab.option_assignment_flow import (
    read_option_assignment_events,
)


FIXTURE = Path("tests/data/schwab/january_assignments.txt")


def test_january_assignment_evidence_identifies_actual_assigned_contracts() -> None:
    events = read_option_assignment_events(
        FIXTURE.read_text(encoding="utf-8").splitlines()
    )

    assert len(events) == 2

    expected = {
        "DXCM": {
            "expiration": date(2026, 1, 9),
            "strike": Decimal("67.00"),
        },
        "EL": {
            "expiration": date(2026, 1, 9),
            "strike": Decimal("106.00"),
        },
    }

    seen: set[str] = set()

    for event in events:
        assert event.kind is PositionEventKind.ASSIGNMENT
        assert event.occurred_at.date() == date(2026, 1, 9)
        assert len(event.changes) == 2

        option_change = next(
            change
            for change in event.changes
            if isinstance(change.instrument, OptionContract)
        )
        equity_change = next(
            change
            for change in event.changes
            if isinstance(change.instrument, Instrument)
            and not isinstance(change.instrument, OptionContract)
        )

        option = option_change.instrument
        underlying = option.underlying

        assert underlying in expected
        assert underlying not in seen
        seen.add(underlying)

        assert option.expiration == expected[underlying]["expiration"]
        assert option.strike == expected[underlying]["strike"]
        assert option.option_type is OptionType.CALL

        assert option_change.quantity == Decimal("5.0000")
        assert equity_change.instrument.symbol == underlying
        assert equity_change.quantity == Decimal("-500.0000")

    assert seen == {"DXCM", "EL"}
