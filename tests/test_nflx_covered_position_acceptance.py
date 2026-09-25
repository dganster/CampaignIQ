from datetime import datetime
from decimal import Decimal
from pathlib import Path

from campaigniq.domain.covered_position import (
    PositionQuantity,
    detect_covered_call_position,
)
from campaigniq.domain.option_contract import OptionContract
from campaigniq.importers.schwab.position_snapshot import to_lots
from campaigniq.importers.schwab.position_snapshot_reader import (
    read_position_snapshot_section,
)


DATA = Path("tests/data")
DECEMBER_POSITIONS = DATA / "schwab/december_positions.txt"


def _underlying(instrument) -> str:
    if isinstance(instrument, OptionContract):
        return instrument.underlying
    return instrument.symbol


def _nflx_positions() -> list[PositionQuantity]:
    rows = read_position_snapshot_section(
        DECEMBER_POSITIONS.read_text().splitlines(),
        snapshot_at=datetime(2025, 12, 31),
    )
    lots = to_lots(list(rows))

    return [
        PositionQuantity(
            instrument=lot.instrument,
            quantity=lot.quantity,
        )
        for lot in lots
        if _underlying(lot.instrument) == "NFLX"
    ]


def test_real_december_snapshot_establishes_fully_covered_nflx_calls() -> None:
    result = detect_covered_call_position(
        _nflx_positions(),
        underlying="NFLX",
    )

    assert result.share_quantity == Decimal("5000")
    assert result.short_call_quantity == Decimal("50")
    assert result.required_share_quantity == Decimal("5000")
    assert result.covered_call_quantity == Decimal("50")
    assert result.uncovered_call_quantity == Decimal("0")
    assert result.excess_share_quantity == Decimal("0")
    assert result.fully_covered


def test_real_snapshot_detection_preserves_broker_quantities() -> None:
    positions = _nflx_positions()

    detect_covered_call_position(
        positions,
        underlying="NFLX",
    )

    assert any(
        not isinstance(position.instrument, OptionContract)
        and position.quantity == Decimal("5000")
        for position in positions
    )
    assert any(
        isinstance(position.instrument, OptionContract)
        and position.quantity == Decimal("-50")
        for position in positions
    )
