"""Read Forex trades from a Thinkorswim Forex Statements section."""

from __future__ import annotations

import csv
import re
from datetime import datetime
from decimal import Decimal

from campaigniq.sources.thinkorswim.section import Section

from .forex_trade_row import ThinkorswimForexTradeRow


_DESCRIPTION_RE = re.compile(
    r"^(?:tIP\s+)?(?P<action>BOT|SOLD)\s+"
    r"(?P<quantity>[+-]?\d+(?:,\d+)?)\s+"
    r"(?P<pair>[A-Z]{3}/[A-Z]{3})\s+"
    r"@(?P<price>(?:\d+(?:\.\d+)?|\.\d+))$",
    re.IGNORECASE,
)


def read_forex_trades(
    section: Section,
) -> list[ThinkorswimForexTradeRow]:
    """Read TRD records from a Forex Statements section."""

    rows: list[ThinkorswimForexTradeRow] = []

    for line in section.data_lines():
        row = next(csv.reader([line]))

        if len(row) < 10:
            continue

        if row[3].strip().upper() != "TRD":
            continue

        description = row[5].strip()
        match = _DESCRIPTION_RE.match(description)

        if match is None:
            raise ValueError(
                f"Unsupported Forex trade description: {description!r}"
            )

        executed_at = datetime.strptime(
            f"{row[1].strip()} {row[2].strip()}",
            "%m/%d/%y %H:%M:%S",
        )

        rows.append(
            ThinkorswimForexTradeRow(
                executed_at=executed_at,
                reference=row[4].strip().lstrip("=").strip('"'),
                action=match.group("action").upper(),
                quantity=Decimal(
                    match.group("quantity").replace(",", "")
                ),
                pair=match.group("pair").upper(),
                price=Decimal(match.group("price")),
                broker_pnl_usd=_parse_broker_pnl(row[8]),
            )
        )

    return rows


def _parse_broker_pnl(value: str) -> Decimal | None:
    """Parse Schwab's Amount(USD) value."""

    value = value.strip()

    if not value or value == "--":
        return None

    negative = value.startswith("(") and value.endswith(")")

    if negative:
        value = value[1:-1]

    value = value.replace("$", "").replace(",", "")

    result = Decimal(value)

    return -result if negative else result
