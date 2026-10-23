"""Corroborate October 2025 NFLX export strikes against actual Schwab trades.

Some later TOS exports divide historical strikes by ten without changing the
execution quantities or prices. This is a source-identity repair, not a split
of lots or cash. Only this verified pre-split month/layout is supported. Original
source bytes and every execution fact remain unchanged.
"""
from collections import defaultdict
from dataclasses import replace
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import re

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.importers.schwab.option_assignment_section_reader import _read_statement_period


START, END = date(2025, 10, 1), date(2025, 10, 31)
# Exact execution/processing pairs corroborated by the supplied October records.
# October 10 processed October 14; do not generalize this to arbitrary holidays.
PROCESSING = {date(2025, 10, 9): date(2025, 10, 10),
              date(2025, 10, 10): date(2025, 10, 14),
              date(2025, 10, 30): date(2025, 10, 31)}
ROW = re.compile(
    r"^\s*(?:\d{2}/\d{2}\s+)?(Sale|Purchase)\s+(Short Sale|Cover Short)\s+NFLX\s+"
    r"(PUT|CALL)\b.*?\$([\d,]+(?:\.\d+)?)\s+EXP\s+"
    r"(\(?[\d,]+\.\d+\)?)\s+([\d,]+\.\d+)\s+"
    r"([\d,]+\.\d+)\s+(\(?[\d,]+\.\d+\)?)(?:\s|$)"
)


def number(value):
    return Decimal(value.replace(',', '').replace('(', '-').replace(')', ''))


def statement_rows(lines):
    rows = []
    processed = None
    active = False
    for i, line in enumerate(lines):
        if 'Transaction Details' in line:
            active = True
        if not active:
            continue
        dated = re.match(r"\s*(\d{2})/(\d{2})(?:\s|$)", line)
        if dated:
            processed = date(2025, int(dated[1]), int(dated[2]))
        m = ROW.match(line)
        if not m:
            continue
        category, action, kind, strike, signed_qty, price, fee, cash = m.groups()
        if (category, action) not in {('Sale', 'Short Sale'), ('Purchase', 'Cover Short')}:
            raise ValueError('Contradictory NFLX transaction action.')
        if processed is None or not START <= processed <= END or i + 2 >= len(lines):
            raise ValueError('Missing October NFLX processing date or contract details.')
        dates = re.findall(r"\b(\d{2}/\d{2}/\d{4}|\d{2}/\d{2}/\d{2})\b", lines[i+1])
        from datetime import datetime
        expirations = {datetime.strptime(d, '%m/%d/%Y' if len(d) == 10 else '%m/%d/%y').date() for d in dates}
        detail = re.match(r"\s*([\d,]+\.\d+)\s+([PC])(?:\s|$)", lines[i+2])
        if (len(dates) != 2 or len(expirations) != 1 or detail is None
                or number(detail[1]) != number(strike) or detail[2] != kind[0]):
            raise ValueError('Uncorroborated NFLX statement contract details.')
        qty, px, fees, amount = map(number, (signed_qty, price, fee, cash))
        sale = action == 'Short Sale'
        if (not qty or (qty < 0) != sale or px <= 0 or fees < 0
                or amount != ((-qty * px * 100).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) - fees)):
            raise ValueError('NFLX statement quantity, price, fee and cash disagree.')
        contract = OptionContract('NFLX', expirations.pop(), number(strike), OptionType(kind))
        row = (processed, contract, Side.SELL if sale else Side.BUY,
               PositionEffect.OPEN if sale else PositionEffect.CLOSE, abs(qty), px)
        if row in rows:
            raise ValueError('Duplicate NFLX statement transaction.')
        rows.append(row)
    return rows


def corroborate_pre_split_contracts(trades, statements, *, start, end):
    """Return derived trades only when both sources establish the contract units.

    No statement or no tenfold discrepancy preserves the existing import path.
    Once a repair is proposed, every NFLX option fill in this month must match
    exactly one independent transaction; incomplete or ambiguous evidence blocks.
    """
    trades = tuple(trades)
    if (start, end) != (START, END):
        return trades
    groups = defaultdict(list)
    for trade in trades:
        for leg in trade.legs:
            if isinstance(leg.instrument, OptionContract) and leg.instrument.underlying == 'NFLX':
                for execution in leg.executions:
                    groups[(execution.executed_at.date(), leg.instrument, leg.side, leg.position_effect)].append(execution)
    if not groups:
        return trades
    # Repeated copies of the same whole statement are one evidence source.
    sources = list(dict.fromkeys(tuple(lines) for lines in statements))
    candidates = []
    for lines in sources:
        if not any(ROW.match(line) for line in lines):
            continue
        if _read_statement_period(list(lines)) != (START, END):
            raise ValueError('NFLX contract evidence must cover October 2025 exactly.')
        candidates.append(statement_rows(lines))
    if not candidates:
        return trades
    if len(candidates) != 1:
        raise ValueError('Ambiguous NFLX statement evidence.')
    rows = candidates[0]
    mappings, used = {}, set()
    for key, executions in groups.items():
        day, contract, side, effect = key
        qty = sum((abs(e.quantity) for e in executions), Decimal(0))
        if (qty <= 0 or any(not e.quantity.is_finite() or e.quantity == 0
                or (e.quantity < 0) != (side is Side.SELL)
                or not e.execution_price.is_finite() or e.execution_price <= 0 for e in executions)):
            raise ValueError('Invalid NFLX execution facts.')
        px = (sum((abs(e.quantity) * e.execution_price for e in executions), Decimal(0)) / qty).quantize(Decimal('.0001'), rounding=ROUND_HALF_UP)
        adjusted = replace(contract, strike=contract.strike * 10)
        matches = [i for i, row in enumerate(rows) if row[0] == PROCESSING.get(day)
                   and row[1] in (contract, adjusted) and row[2:] == (side, effect, qty, px)]
        if len(matches) != 1 or matches[0] in used:
            raise ValueError('NFLX fills lack unique statement quantity/price/date corroboration.')
        i = matches[0]
        used.add(i)
        mappings[key] = rows[i][1]
    if not any(key[1] != value for key, value in mappings.items()):
        return trades
    if len(used) != len(rows):
        raise ValueError('Incomplete NFLX execution coverage; contract correction refused.')
    result = []
    for trade in trades:
        legs = []
        for leg in trade.legs:
            if isinstance(leg.instrument, OptionContract) and leg.instrument.underlying == 'NFLX':
                contracts = {mappings[(e.executed_at.date(), leg.instrument, leg.side, leg.position_effect)] for e in leg.executions}
                if len(contracts) != 1:
                    raise ValueError('Mixed NFLX contract units within one leg.')
                corrected = contracts.pop()
                leg = replace(leg, **({'contract': corrected} if isinstance(leg, OptionLeg) else {'instrument': corrected}))
            legs.append(leg)
        result.append(replace(trade, legs=tuple(legs)))
    return tuple(result)
