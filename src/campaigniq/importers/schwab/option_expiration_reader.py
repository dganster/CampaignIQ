"""Read explicitly labeled option expirations from Schwab statement transaction details.

Only broker-recorded Expired Long/Expired Short activity creates events. A
closing-position snapshot or an option's calendar expiration is not evidence.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
import re

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind

_HEADER = re.compile(
    r"^\s*(?:(?P<month>\d{2})/(?P<day>\d{2})\s+)?Other\s+"
    r"Expired\s+(?:Long|Short)\s+(?P<symbol>[A-Z][A-Z0-9.]*)\s+"
    r"(?P<type>CALL|PUT)\b.*?\s+\$(?P<strike>[\d,]+(?:\.\d+)?)\s+"
    r"(?P<quantity>\([\d,]+(?:\.\d+)?\)|[\d,]+(?:\.\d+)?)\s*$"
)
_CONTRACT = re.compile(r"\b(?P<expiration>\d{2}/\d{2}/\d{4})\s+EXP\s+\d{2}/\d{2}/\d{2}\b")
_STRIKE_TYPE = re.compile(r"^\s*(?P<strike>[\d,]+(?:\.\d+)?)\s+(?P<type>[CP])\s*$")
_DATE = re.compile(r"^\s*(\d{2})/(\d{2})(?:\s|$)")


def read_option_expiration_events(
    lines: list[str], *, start: date, end: date,
) -> tuple[PositionEvent, ...]:
    """Return signed, contract-specific events from Schwab's explicit activity rows."""
    events = []
    transaction_day: tuple[int, int] | None = None
    for index, line in enumerate(lines):
        dated = _DATE.match(line)
        if dated:
            transaction_day = (int(dated[1]), int(dated[2]))
        match = _HEADER.match(line)
        if match is None:
            continue
        if index + 2 >= len(lines):
            raise ValueError('Truncated Schwab option expiration activity')
        contract_line = _CONTRACT.search(lines[index + 1])
        strike_line = _STRIKE_TYPE.match(lines[index + 2])
        if contract_line is None or strike_line is None:
            raise ValueError('Schwab option expiration lacks contract evidence')
        expiration = datetime.strptime(contract_line['expiration'], '%m/%d/%Y').date()
        option_type = OptionType.CALL if match['type'] == 'CALL' else OptionType.PUT
        strike = Decimal(match['strike'].replace(',', ''))
        if (strike != Decimal(strike_line['strike'].replace(',', ''))
                or strike_line['type'] != ('C' if option_type is OptionType.CALL else 'P')):
            raise ValueError('Schwab option expiration contract disagrees across lines')
        day = ((int(match['month']), int(match['day']))
               if match['month'] else transaction_day)
        if day is None:
            raise ValueError('Schwab option expiration missing transaction date')
        transaction_date = date(expiration.year, *day)
        if not start <= transaction_date <= end:
            continue
        raw_quantity = match['quantity']
        quantity = Decimal(raw_quantity.strip('()').replace(',', ''))
        if raw_quantity.startswith('('):
            quantity = -quantity
        if quantity == 0:
            raise ValueError('Zero-quantity Schwab expiration')
        # SPXW is the brokerage activity symbol for weekly SPX index options.
        symbol = 'SPX' if match['symbol'] == 'SPXW' else match['symbol']
        events.append(PositionEvent(
            kind=PositionEventKind.EXPIRATION,
            changes=(PositionChange(OptionContract(symbol, expiration, strike, option_type), quantity),),
            occurred_at=datetime.combine(transaction_date, time.max),
        ))
    return tuple(events)
