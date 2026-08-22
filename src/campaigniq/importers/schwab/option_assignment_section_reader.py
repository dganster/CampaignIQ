"""Read option-assignment records from Schwab statement text."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from campaigniq.importers.schwab.option_assignment_row import (
    SchwabOptionAssignmentRow,
)


def read_option_assignment_section(
    lines: list[str],
) -> list[SchwabOptionAssignmentRow]:
    """Read option-assignment records from statement lines."""

    rows: list[SchwabOptionAssignmentRow] = []

    index = 0

    while index < len(lines):
        if lines[index].strip() != "Option Assignment":
            index += 1
            continue

        symbol = lines[index + 1].strip()
        contract_parts = lines[index + 2].strip().split()

        if len(contract_parts) != 3:
            raise ValueError(
                "Invalid option assignment contract: "
                f"{lines[index + 2]!r}"
            )

        expiration = _parse_date(contract_parts[0])
        strike = Decimal(contract_parts[1])
        option_code = contract_parts[2].upper()

        transaction_date = _parse_transaction_date(
            lines,
            index,
            year=expiration.year,
        )

        if option_code not in {"C", "P"}:
            raise ValueError(
                f"Unsupported option type: {option_code!r}"
            )

        quantity_index = index + 4

        if quantity_index >= len(lines):
            raise ValueError(
                "Option Assignment is missing quantity."
            )

        quantity = Decimal(lines[quantity_index].strip())

        rows.append(
            SchwabOptionAssignmentRow(
                transaction_date=transaction_date,
                symbol=symbol,
                expiration=expiration,
                strike=strike,
                option_type=(
                    "CALL" if option_code == "C" else "PUT"
                ),
                quantity=quantity,
            )
        )

        index = quantity_index + 1

    return rows

def _parse_transaction_date(
    lines: list[str],
    assignment_index: int,
    *,
    year: int,
) -> date:
    """Find the transaction date associated with an assignment."""

    for index in range(assignment_index - 1, -1, -1):
        value = lines[index].strip()

        if value.startswith("Trade Date:"):
            trade_date = value.removeprefix("Trade Date:").strip()

            month, day, short_year = trade_date.split("/")
            return date(2000 + int(short_year), int(month), int(day))

        try:
            month, day = value.split("/")
            return date(year, int(month), int(day))
        except (ValueError, TypeError):
            continue

    raise ValueError(
        "Could not find transaction date for Option Assignment."
    )

def _parse_date(value: str) -> date:
    """Parse an MM/DD/YYYY date."""

    month, day, year = value.split("/")

    return date(int(year), int(month), int(day))
