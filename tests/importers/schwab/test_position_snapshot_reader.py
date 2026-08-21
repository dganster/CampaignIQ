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

def test_reads_march_schwab_ending_equity_positions() -> None:
    fixture = Path("tests/data/schwab/march_positions.txt")

    rows = read_position_snapshot_section(
        fixture.read_text().splitlines(),
        snapshot_at=datetime(2026, 3, 31),
    )
    equities = [row for row in rows if row.expiration is None]

    assert len(equities) == 9

    csco = next(row for row in equities if row.symbol == "CSCO")
    assert csco.quantity == Decimal("500")
    assert csco.basis_total == Decimal("40510.00")

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

def test_reads_other_assets_as_equity_positions() -> None:
    lines = [
        "Positions - Other Assets",
        "Symbol       Description                                                                           Quantity           Price($)     Market Value($)              Cost Basis($)          Gain/(Loss)($)        Yield       Income($)",
        "",
        "AMT          AMERICAN TOWER CORP NEW (M),                                                       100.0000         186.96000              18,696.00                18,171.00                   525.00     3.82%               716.00",
        "             REIT",
        "",
        " Total Other Assets",
    ]

    rows = read_position_snapshot_section(
        lines,
        snapshot_at=datetime(2026, 5, 31, 23, 59, 59),
    )

    amt = next(row for row in rows if row.symbol == "AMT")

    assert amt.quantity == Decimal("100.0000")
    assert amt.basis_total == Decimal("18171.00")
    assert amt.instrument() == Instrument("AMT")


def test_reads_wrapped_gs_price_from_schwab_pdf_extraction() -> None:
    lines = [
        "Positions - Equities",
        "GS          GOLDMAN SACHS GROUP INC                                                         100.0000          1,025.5600             102,556.00              92,053.00           10,503.00     1.75%            1,800.00",
        "                                                                                                                       0",
        "Total Equities",
    ]

    rows = read_position_snapshot_section(
        lines,
        snapshot_at=datetime(2026, 5, 31, 23, 59, 59),
    )

    gs = next(row for row in rows if row.symbol == "GS")

    assert gs.quantity == Decimal("100.0000")
    assert gs.basis_total == Decimal("92053.00")


def test_reads_may_schwab_ending_position_inventory() -> None:
    fixture = Path("tests/data/schwab/may_positions.txt")

    rows = read_position_snapshot_section(
        fixture.read_text().splitlines(),
        snapshot_at=datetime(2026, 5, 31, 23, 59, 59),
    )

    assert len(rows) == 39
    assert sum(row.expiration is None for row in rows) == 19
    assert sum(row.expiration is not None for row in rows) == 20

    amt_stock = next(
        row for row in rows
        if row.symbol == "AMT" and row.expiration is None
    )
    assert amt_stock.quantity == Decimal("100")
    assert amt_stock.basis_total == Decimal("18171.00")

    amt_call = next(
        row for row in rows
        if row.symbol == "AMT" and row.expiration is not None
    )
    assert amt_call.quantity == Decimal("-1")
    assert amt_call.expiration.isoformat() == "2026-06-18"
    assert amt_call.strike == Decimal("155")
    assert amt_call.option_type == OptionType.CALL
    assert amt_call.basis_total == Decimal("-1513.31")
