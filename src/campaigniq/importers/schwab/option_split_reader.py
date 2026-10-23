"""Read the explicitly corroborated NFLX November 2025 option forward split.

OCC memo 57651: effective 2025-11-17, contract multiplier 10,
strike divisor 10, unchanged 100-share deliverable.
https://infomemo.theocc.com/infomemos?number=57651
No split is inferred from mismatched closing inventory.
"""
from datetime import date, datetime
from decimal import Decimal
import re

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.importers.schwab.option_assignment_section_reader import _read_statement_period
from campaigniq.importers.schwab.position_snapshot_reader import read_position_snapshot_section

_HEADER = re.compile(
    r'^\s*(?:\d{2}/\d{2}\s+)?Other\s+Option Fwd Split\s+'
    r'(?P<symbol>[A-Z][A-Z0-9.]*)\s+(?P<type>PUT|CALL)\b.*?'
    r'\$(?P<strike>[\d,]+(?:\.\d+)?)\s+EXP\s+'
    r'(?P<quantity>\([\d,]+(?:\.\d+)?\)|[\d,]+(?:\.\d+)?)\s*$')
_DETAIL = re.compile(r'^\s*Activity\s+(\d{2}/\d{2}/\d{4})\s+(?:EXP\s+)?(\d{2}/\d{2}/\d{2})\s*$')
_STRIKE = re.compile(r'^\s*([\d,]+(?:\.\d+)?)\s+([CP])\s*$')


def read_option_split_events(lines, *, opening_lot_book, trades, start, end):
    candidates = [i for i, line in enumerate(lines) if 'Option Fwd Split' in line]
    if not candidates:
        return ()
    period = _read_statement_period(lines)
    if period != (start, end):
        raise ValueError('Option split requires the selected monthly statement period.')
    events = []
    closing = read_position_snapshot_section(lines, snapshot_at=datetime.combine(end, datetime.max.time()))
    for index in candidates:
        match = _HEADER.fullmatch(lines[index])
        if match is None or index + 2 >= len(lines):
            raise ValueError('Unrecognized or truncated option split evidence.')
        detail, strike = _DETAIL.fullmatch(lines[index + 1]), _STRIKE.fullmatch(lines[index + 2])
        if detail is None or strike is None:
            raise ValueError('Option split lacks complete contract evidence.')
        expiration = datetime.strptime(detail[1], '%m/%d/%Y').date()
        amount = Decimal(match['strike'].replace(',', ''))
        if (datetime.strptime(detail[2], '%m/%d/%y').date() != expiration
                or Decimal(strike[1].replace(',', '')) != amount
                or strike[2] != ('P' if match['type'] == 'PUT' else 'C')):
            raise ValueError('Option split contract details disagree.')
        day = None
        for line in reversed(lines[:index + 1]):
            found = re.match(r'^\s*(\d{2})/(\d{2})(?:\s|$)', line)
            if found:
                day = date(start.year, int(found[1]), int(found[2]))
                break
        if match['symbol'] != 'NFLX' or day != date(2025, 11, 17):
            raise ValueError('Option split is not covered by verified OCC evidence.')
        if any(getattr(leg.instrument, 'underlying', getattr(leg.instrument, 'symbol', None)) == 'NFLX'
               for trade in trades for leg in trade.legs):
            raise ValueError('NFLX split-month trades require separately supported chronology.')
        new = OptionContract('NFLX', expiration, amount, OptionType[match['type']])
        old = OptionContract('NFLX', expiration, amount * Decimal(10), OptionType[match['type']])
        lots = opening_lot_book.lots(old)
        quantity = sum((lot.quantity for lot in lots), Decimal(0))
        text = match['quantity'].replace(',', '')
        delta = -Decimal(text[1:-1]) if text.startswith('(') else Decimal(text)
        snapshot_quantity = sum((row.quantity for row in closing if row.instrument() == new), Decimal(0))
        if (not lots or not quantity or opening_lot_book.lots(new)
                or delta != quantity * Decimal(9) or snapshot_quantity != quantity * Decimal(10)
                or any(row.instrument() == old for row in closing)):
            raise ValueError('Option split opening, explicit adjustment and closing units disagree.')
        event = PositionEvent(PositionEventKind.SPLIT,
            (PositionChange(old, -quantity), PositionChange(new, quantity * Decimal(10))),
            datetime.combine(day, datetime.min.time()))
        # Check lot dates, signs and all transformation invariants on a clone.
        opening_lot_book.clone().apply_event(event)
        if any(e.changes[0].instrument == old for e in events):
            raise ValueError('Duplicate option split evidence.')
        events.append(event)
    return tuple(events)
