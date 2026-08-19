from datetime import datetime
from decimal import Decimal
from pathlib import Path

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.schwab.position_snapshot_reader import (
    read_position_snapshot_section,
)


FIXTURE = Path("tests/data/schwab/january_positions.txt")


def test_reads_january_schwab_ending_equity_positions() -> None:
    rows = read_position_snapshot_section(
        FIXTURE.read_text().splitlines(),
        snapshot_at=datetime(2026, 1, 31),
    )

    equities = [row for row in rows if row.expiration is None]

    assert len(equities) == 10
    assert equities[0].symbol == "CAT"
    assert equities[0].quantity == Decimal("200")
    assert equities[0].basis_total == Decimal("125498.99")

    lin = next(row for row in equities if row.symbol == "LIN")
    assert lin.quantity == Decimal("500")
    assert lin.basis_total == Decimal("213921.31")


def test_reads_january_schwab_ending_option_positions() -> None:
    rows = read_position_snapshot_section(
        FIXTURE.read_text().splitlines(),
        snapshot_at=datetime(2026, 1, 31),
    )

    options = [row for row in rows if row.expiration is not None]

    assert len(options) == 14

    lin = next(row for row in options if row.symbol == "LIN")
    assert lin.quantity == Decimal("-5")
    assert lin.expiration.isoformat() == "2026-02-20"
    assert lin.strike == Decimal("365")
    assert lin.option_type == OptionType.CALL
    assert lin.basis_total == Decimal("-32046.69")


def test_option_snapshot_preserves_full_contract_identity() -> None:
    rows = read_position_snapshot_section(
        FIXTURE.read_text().splitlines(),
        snapshot_at=datetime(2026, 1, 31),
    )

    nflx = next(
        row for row in rows if row.symbol == "NFLX" and row.expiration is not None
    )

    assert nflx.instrument() == OptionContract(
        underlying="NFLX",
        expiration=nflx.expiration,
        strike=Decimal("78"),
        option_type=OptionType.CALL,
    )


def test_snapshot_contains_both_long_equities_and_short_options() -> None:
    rows = read_position_snapshot_section(
        FIXTURE.read_text().splitlines(),
        snapshot_at=datetime(2026, 1, 31),
    )

    assert any(row.instrument() == Instrument("CAT") for row in rows)
    assert any(row.quantity < 0 for row in rows if row.expiration is not None)
