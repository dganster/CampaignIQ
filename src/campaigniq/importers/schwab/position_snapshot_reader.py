"""Read Schwab brokerage-statement ending positions."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from campaigniq.importers.schwab.position_snapshot import (
    SchwabPositionSnapshotRow,
)
from campaigniq.domain.option_type import OptionType


_EQUITY_RE = re.compile(
    r"^\s*(?P<symbol>\S+)\s+.*?"
    r"(?P<quantity>\(?-?[\d,]+\.\d{4}\)?)\s+"
    r"(?P<price>[\d,]+\.\d{4,5})\s+"
    r"(?P<market>\(?-?[\d,]+\.\d{2}\)?)\s+"
    r"(?P<basis>\(?-?[\d,]+\.\d{2}\)?)\s+"
    r"(?P<gain>\(?-?[\d,]+\.\d{2}\)?)"
    r"(?:\s+(?:N/A|[\d.]+%))?"
    r"(?:\s+(?:N/A|\(?-?[\d,]+\.\d{2}\)?))?\s*$"
)

_OPTION_RE = re.compile(
    r"^\s*(?P<symbol>\S+)\s+"
    r"(?P<option_type>CALL|PUT)\b.*?"
    r"(?P<quantity>\([\d,]+\.\d{4}\))\s+S\s+"
    r"[\d,]+\.\d{5}\s+"
    r"\(?[\d,]+\.\d{2}\)?\s+"
    r"(?P<basis>\([\d,]+\.\d{2}\))\s+"
    r"\(?[\d,]+\.\d{2}\)?\s*$"
)

_CONTRACT_RE = re.compile(
    r"\$(?P<strike>[\d,]+(?:\.\d+)?)\s+EXP\s+"
    r"(?P<expiration>\d{2}/\d{2}/\d{2})"
)


def read_position_snapshot_section(
    lines: list[str],
    *,
    snapshot_at: datetime,
) -> tuple[SchwabPositionSnapshotRow, ...]:
    """Read equity and option positions from extracted Schwab statement text.

    The Schwab monthly statement reports the positions as of the statement
    period end.  This reader therefore produces a generic position snapshot;
    callers may use that snapshot as the following month's opening inventory.
    """

    rows: list[SchwabPositionSnapshotRow] = []
    section = None
    index = 0

    while index < len(lines):
        line = lines[index].strip()

        if line.startswith("Positions - Equities"):
            section = "equities"
            index += 1
            continue

        if line.startswith("Positions - Other Assets"):
            section = "equities"
            index += 1
            continue

        if line.startswith("Positions - Options"):
            section = "options"
            index += 1
            continue

        if line.startswith("Total Equities"):
            section = None
            index += 1
            continue

        if line.startswith("Total Options"):
            section = None
            index += 1
            continue

        if section == "equities":
            match = _EQUITY_RE.match(lines[index])
            if match:
                rows.append(
                    SchwabPositionSnapshotRow(
                        symbol=match.group("symbol"),
                        quantity=_parse_amount(match.group("quantity")),
                        snapshot_at=snapshot_at,
                        basis_total=_parse_amount(match.group("basis")),
                    )
                )

        elif section == "options":
            match = _OPTION_RE.match(lines[index])
            if match:
                contract = _find_contract(lines, index)
                if contract is None:
                    raise ValueError(
                        f"Missing option contract details for {match.group('symbol')}."
                    )

                expiration, strike = contract
                rows.append(
                    SchwabPositionSnapshotRow(
                        symbol=match.group("symbol"),
                        quantity=_parse_amount(match.group("quantity")),
                        snapshot_at=snapshot_at,
                        expiration=expiration,
                        strike=strike,
                        option_type=OptionType[match.group("option_type")],
                        basis_total=_parse_amount(match.group("basis")),
                    )
                )

        index += 1

    return tuple(rows)


def _find_contract(
    lines: list[str],
    row_index: int,
):
    for line in lines[row_index + 1 : row_index + 5]:
        match = _CONTRACT_RE.search(line)
        if match:
            expiration = datetime.strptime(
                match.group("expiration"), "%m/%d/%y"
            ).date()
            strike = Decimal(match.group("strike").replace(",", ""))
            return expiration, strike
    return None


def _parse_amount(value: str) -> Decimal:
    value = value.strip().replace(",", "")
    if value.startswith("(") and value.endswith(")"):
        return -Decimal(value[1:-1])
    return Decimal(value)

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
