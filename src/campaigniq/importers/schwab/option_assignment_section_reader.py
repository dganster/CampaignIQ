"""Read option-assignment records from Schwab statement text."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import re

from campaigniq.importers.schwab.option_assignment_row import (
    SchwabOptionAssignmentRow,
)


def read_option_assignment_section(
    lines: list[str],
) -> list[SchwabOptionAssignmentRow]:
    """Read option-assignment records from statement lines."""

    rows: list[SchwabOptionAssignmentRow] = []

    statement_period = _read_statement_period(lines) if any(
        "Assignment" in line for line in lines
    ) else None
    index = 0

    while index < len(lines):
        layout_row = _read_layout_assignment(lines, index, statement_period=statement_period)
        if layout_row is not None:
            row, index = layout_row
            rows.append(row)
            continue
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
            statement_period=statement_period,
        )

        trade_date = _parse_trade_date(
            lines,
            index,
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
                trade_date=trade_date,
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


def _read_layout_assignment(
    lines: list[str], index: int,
    *, statement_period: tuple[date, date] | None = None,
) -> tuple[SchwabOptionAssignmentRow, int] | None:
    """Read an explicitly labeled assignment from Poppler's table layout."""
    line = lines[index]
    header = re.search(r"\bOther\s+Option\s+(?P<symbol>[A-Z][A-Z0-9./-]*)\b", line)
    if header is None or index + 1 >= len(lines):
        return None
    if not re.search(r"\bActivity\s+Assignment\b", lines[index + 1]):
        return None
    description = re.search(r"\b(CALL|PUT)\b", line)
    quantity = re.search(r"\s+(\d[\d,]*\.\d+)\s*$", line)
    if description is None or quantity is None:
        raise ValueError("Option Assignment is missing type or quantity.")
    start = header.start("symbol")
    stop = description.start()
    contract_lines = [line[start:stop]]
    end = index + 1
    while end < len(lines) and end <= index + 3:
        continuation = lines[end]
        if end > index + 1 and (
            not continuation.strip()
            or re.match(r"\s*\d{2}/\d{2}\s", continuation)
            or re.search(r"\bOther\s+Option\b", continuation)
        ):
            break
        contract_lines.append(continuation[start:stop])
        end += 1
    contract = " ".join(contract_lines)
    parsed = re.search(r"(\d{2}/\d{2}/\d{4})\s+(\d[\d,]*\.\d+)\s+([CP])\b", contract)
    if parsed is None:
        raise ValueError("Invalid wrapped Option Assignment contract.")
    expiration = _parse_date(parsed[1])
    option_type = description[1]
    if parsed[3] != ("C" if option_type == "CALL" else "P"):
        raise ValueError("Option Assignment type disagrees with contract.")
    transaction_date = None
    for previous in reversed(lines[:index + 1]):
        dated = re.match(r"\s*(\d{2})/(\d{2})(?:\s|$)", previous)
        if dated:
            transaction_date = _resolve_transaction_date(
                int(dated[1]), int(dated[2]),
                fallback_year=expiration.year, statement_period=statement_period,
            )
            break
    if transaction_date is None:
        raise ValueError("Could not find transaction date for Option Assignment.")
    return SchwabOptionAssignmentRow(
        transaction_date=transaction_date,
        trade_date=_parse_trade_date(lines, index),
        symbol=header["symbol"],
        expiration=expiration,
        strike=Decimal(parsed[2].replace(",", "")),
        option_type=option_type,
        quantity=Decimal(quantity[1].replace(",", "")),
    ), end

def _parse_transaction_date(
    lines: list[str],
    assignment_index: int,
    *,
    year: int,
    statement_period: tuple[date, date] | None = None,
) -> date:
    """Find the statement transaction date associated with an assignment."""

    for index in range(assignment_index - 1, -1, -1):
        value = lines[index].strip()

        try:
            month, day = value.split("/")
            month, day = int(month), int(day)
        except (ValueError, TypeError):
            continue
        return _resolve_transaction_date(
            month, day, fallback_year=year, statement_period=statement_period,
        )

    raise ValueError(
        "Could not find transaction date for Option Assignment."
    )

def _parse_trade_date(
    lines: list[str],
    assignment_index: int,
) -> date | None:
    """Find an explicit Schwab trade date when present."""

    for index in range(assignment_index - 1, -1, -1):
        value = lines[index].strip()

        explicit_date = re.search(r"\bTrade Date:\s*(\d{2})/(\d{2})/(\d{2})\b", value)
        if explicit_date:
            month, day, short_year = explicit_date.groups()
            return date(2000 + int(short_year), int(month), int(day))
        if re.match(r"^\d{2}/\d{2}(?:\s|$)", value):
            return None

        try:
            month, day = value.split("/")
            int(month)
            int(day)
            return None
        except (ValueError, TypeError):
            continue

    return None

def _parse_date(value: str) -> date:
    """Parse an MM/DD/YYYY date."""

    month, day, year = value.split("/")

    return date(int(year), int(month), int(day))


_MONTHS = {
    name: number for number, name in enumerate((
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ), start=1)
}
_MONTHLY_PERIOD = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})\s*[-–]\s*"
    r"(\d{1,2}),\s*(\d{4})\b"
)


def _read_statement_period(lines: list[str]) -> tuple[date, date] | None:
    """Read explicit monthly statement headers, never an option's expiry year.

    Headerless legacy text fragments retain their existing year convention.
    A full statement with an unreadable or conflicting period fails closed.
    """
    periods = set()
    for index, line in enumerate(lines):
        if not (line.lstrip().startswith("Statement Period") or re.search(
            r"\bStatement Period(?:\s*$|\s+(?:" + "|".join(_MONTHS) + r")\b)",
            line,
        )):
            continue
        match = _MONTHLY_PERIOD.search(" ".join(lines[index:index + 6]))
        if match is None:
            raise ValueError("Unreadable statement period for Option Assignment.")
        month, first, last, year = match.groups()
        start = date(int(year), _MONTHS[month], int(first))
        end = date(int(year), _MONTHS[month], int(last))
        if end < start:
            raise ValueError("Invalid statement period for Option Assignment.")
        periods.add((start, end))
    if len(periods) > 1:
        raise ValueError("Conflicting statement periods for Option Assignment.")
    return next(iter(periods), None)


def _resolve_transaction_date(
    month: int, day: int, *, fallback_year: int,
    statement_period: tuple[date, date] | None,
) -> date:
    if statement_period is None:
        return date(fallback_year, month, day)
    start, end = statement_period
    candidates = []
    for year in range(start.year, end.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if start <= candidate <= end:
            candidates.append(candidate)
    if len(candidates) != 1:
        raise ValueError("Option Assignment transaction date is outside statement period.")
    return candidates[0]
