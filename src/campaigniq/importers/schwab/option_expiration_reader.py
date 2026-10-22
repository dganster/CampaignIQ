"""Read explicit Schwab option expiration transactions from layout-preserving text.

Never infer expiration from a closing snapshot or the option's calendar date.
"""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
import re

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind

# Schwab has at least two layouts: older three-line SPXW records and newer
# two-line records with the full contract date on the activity header.
_HEADER = re.compile(
    r"^\s*(?:(?P<month>\d{2})/(?P<day>\d{2})\s+)?Other\s+"
    r"Expired\s+(?P<side>Long|Short)\s+(?P<symbol>[A-Z][A-Z0-9.]*)\s+"
    r"(?:(?P<inline_expiration>\d{2}/\d{2}/\d{4})\s+)?"
    r"(?P<type>CALL|PUT)\b.*?\s+\$(?P<strike>[\d,]+(?:\.\d+)?)\b.*?\s+"
    r"(?P<quantity>\([\d,]+(?:\.\d+)?\)|[\d,]+(?:\.\d+)?)\s*$"
)
_OLD_CONTRACT = re.compile(r"\b(?P<expiration>\d{2}/\d{2}/\d{4})\s+EXP\s+\d{2}/\d{2}/\d{2}\b")
_OLD_STRIKE = re.compile(r"^\s*(?P<strike>[\d,]+(?:\.\d+)?)\s+(?P<type>[CP])\s*$")
_NEW_DETAIL = re.compile(r"^\s*Activity\s+(?P<strike>[\d,]+(?:\.\d+)?)\s+(?P<type>[CP])\s+(?P<expiration>\d{2}/\d{2}/\d{2})\s*$")
_DATE = re.compile(r"^\s*(\d{2})/(\d{2})(?:\s|$)")
_CANDIDATE = re.compile(r"\bExpired\s+(?:Long|Short)\b")


def read_option_expiration_events(lines: list[str], *, start: date, end: date) -> tuple[PositionEvent, ...]:
    """Return signed, contract-specific events from explicit Schwab activity."""
    events = []
    transaction_day: tuple[int, int] | None = None
    for index, line in enumerate(lines):
        dated = _DATE.match(line)
        if dated:
            transaction_day = (int(dated[1]), int(dated[2]))
        if not _CANDIDATE.search(line):
            continue
        match = _HEADER.match(line)
        if match is None:
            raise ValueError(f'Unrecognized Schwab option expiration at line {index + 1}')
        if index + 1 >= len(lines):
            raise ValueError('Truncated Schwab option expiration activity')
        option_type = OptionType.CALL if match['type'] == 'CALL' else OptionType.PUT
        strike = Decimal(match['strike'].replace(',', ''))
        inline_expiration = match['inline_expiration']
        if inline_expiration:
            detail = _NEW_DETAIL.match(lines[index + 1])
            if detail is None:
                raise ValueError('Schwab option expiration lacks contract evidence')
            expiration = datetime.strptime(inline_expiration, '%m/%d/%Y').date()
            detail_expiration = datetime.strptime(detail['expiration'], '%m/%d/%y').date()
            if detail_expiration != expiration:
                raise ValueError('Schwab option expiration contract disagrees across lines')
        else:
            if index + 2 >= len(lines):
                raise ValueError('Truncated Schwab option expiration activity')
            contract = _OLD_CONTRACT.search(lines[index + 1])
            detail = _OLD_STRIKE.match(lines[index + 2])
            if contract is None or detail is None:
                raise ValueError('Schwab option expiration lacks contract evidence')
            expiration = datetime.strptime(contract['expiration'], '%m/%d/%Y').date()
        if (strike != Decimal(detail['strike'].replace(',', ''))
                or detail['type'] != ('C' if option_type is OptionType.CALL else 'P')):
            raise ValueError('Schwab option expiration contract disagrees across lines')
        day = ((int(match['month']), int(match['day'])) if match['month'] else transaction_day)
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
        symbol = 'SPX' if match['symbol'] == 'SPXW' else match['symbol']
        events.append(PositionEvent(
            kind=PositionEventKind.EXPIRATION,
            changes=(PositionChange(OptionContract(symbol, expiration, strike, option_type), quantity),),
            occurred_at=datetime.combine(transaction_date, time.max),
        ))
    return tuple(events)
