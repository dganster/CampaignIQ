"""Translate Thinkorswim Cash Balance EXP rows into position events."""

import re
from datetime import datetime, date
from decimal import Decimal

from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.thinkorswim.cash_balance_row import (
    ThinkorswimCashBalanceRow,
)


_EXP_DESCRIPTION_RE = re.compile(
    r"^(?P<action>BOT|SOLD)\s+"
    r"(?P<quantity>-?[\d,]+(?:\.\d+)?)\s+"
    r"(?P<symbol>[A-Z.]+)"
    r"(?:\s+100\s+\(Weeklys\)\s+"
    r"(?P<expiry_day>\d{1,2})\s+(?P<expiry_month>[A-Z]{3})\s+"
    r"(?P<expiry_year>\d{2})\s+(?P<strike>[\d,.]+)\s+"
    r"(?P<option_type>PUT|CALL))?"
    r"\s+UPON\b"
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

    if match.group("option_type") is not None:
        expiry = datetime.strptime(
            f"{match.group('expiry_day')} "
            f"{match.group('expiry_month')} "
            f"{match.group('expiry_year')}",
            "%d %b %y",
        ).date()
        instrument = OptionContract(
            underlying=match.group("symbol"),
            expiration=expiry,
            strike=Decimal(match.group("strike").replace(",", "")),
            option_type=OptionType(match.group("option_type")),
        )
    else:
        instrument = Instrument(match.group("symbol"))

    return PositionEvent(
        kind=PositionEventKind.EXPIRATION,
        changes=(
            PositionChange(
                instrument=instrument,
                quantity=quantity,
            ),
        ),
        occurred_at=occurred_at,
    )
