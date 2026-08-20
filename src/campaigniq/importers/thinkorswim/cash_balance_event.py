"""Translate Thinkorswim Cash Balance EXP rows into position events."""

import re
from datetime import datetime
from decimal import Decimal

from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.thinkorswim.cash_balance_row import (
    ThinkorswimCashBalanceRow,
)


_EXP_DESCRIPTION_RE = re.compile(
    r"^(?P<action>BOT|SOLD)\s+"
    r"(?P<quantity>-?[\d,]+(?:\.\d+)?)\s+"
    r"(?P<symbol>[A-Z.]+)\s+UPON\b"
)


def to_expiration_event(
    row: ThinkorswimCashBalanceRow,
) -> PositionEvent:
    """Translate one EXP Cash Balance row into a stock position event."""

    if row.transaction_type != "EXP":
        raise ValueError(
            f"Cash Balance row is not an EXP transaction: "
            f"{row.transaction_type!r}"
        )

    match = _EXP_DESCRIPTION_RE.match(row.description.strip())

    if match is None:
        raise ValueError(
            f"Unsupported EXP description: {row.description!r}"
        )

    quantity = Decimal(
        match.group("quantity").replace(",", "")
    )

    if match.group("action") == "BOT":
        quantity = abs(quantity)
    else:
        quantity = -abs(quantity)

    occurred_at = datetime.combine(
        row.transaction_date,
        row.transaction_time,
    )

    return PositionEvent(
        kind=PositionEventKind.EXPIRATION,
        changes=(
            PositionChange(
                instrument=Instrument(match.group("symbol")),
                quantity=quantity,
            ),
        ),
        occurred_at=occurred_at,
    )
