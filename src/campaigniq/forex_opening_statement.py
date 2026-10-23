"""Evidence-bound Forex opening inventory from a corroborated monthly statement.

This does not replace transaction-report money controls or infer opening units
from a closing trade. Unsupported statement layouts fail closed.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import re

from campaigniq.domain.lot import Lot
from campaigniq.domain.value_objects.forex_pair import ForexPair
from campaigniq.importers.thinkorswim.forex_trade_reader import read_forex_trades
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader

_NUMBER = r'\(?[+-]?[\d,]+(?:\.\d+)?\)?'
_HEADER = re.compile(
    r'Customer Account Summary Report since \w+, (\w+ \d+, \d{4}) '
    r'16:00:00 \(CST\) through \w+, (\w+ \d+, \d{4}) 16:00:00 \(CST\)')
_POSITION = re.compile(rf'([A-Z]{{3}}/[A-Z]{{3}}) - TOTAL POSITION:\s+(Buy|Sell)\s+({_NUMBER})\s+')
_TRADE = re.compile(
    rf'^\s*(\d{{4}}-\d{{2}}-\d{{2}}) \d{{2}}:\d{{2}}:\d{{2}}\s+'
    rf'([A-Z]{{3}}/[A-Z]{{3}})\s+\S+\s+([BS])\s+(OPEN|CLOSE)\s+'
    rf'({_NUMBER})\s+({_NUMBER})\s+(\d+)\s+\$({_NUMBER})\s*$')


def _amount(value):
    value = value.replace(',', '')
    result = -Decimal(value[1:-1]) if value.startswith('(') else Decimal(value)
    if not result.is_finite():
        raise ValueError('Non-finite Forex statement quantity.')
    return result


def _coverage(page):
    match = _HEADER.fullmatch(page.splitlines()[0].strip())
    if match is None:
        raise ValueError('Unsupported Forex statement header or time zone.')
    return tuple(datetime.strptime(v, '%B %d, %Y').date() for v in match.groups())


def _positions(section):
    result = {}
    for line in section.splitlines():
        if 'TOTAL POSITION' not in line:
            continue
        match = _POSITION.match(line.strip())
        if match is None:
            raise ValueError('Unrecognized Forex statement position.')
        pair, side, quantity = match.groups()
        value = _amount(quantity)
        if not value or (side == 'Buy') != (value > 0) or pair in result:
            raise ValueError('Duplicate or inconsistent Forex statement position.')
        result[pair] = value
    if not result and 'TR Details:' in section:
        raise ValueError('Forex statement position totals are missing.')
    return result


@dataclass(frozen=True)
class ForexOpeningEvidence:
    opening: tuple[tuple[str, Decimal], ...]
    closing: tuple[tuple[str, Decimal], ...]
    source_sha256: str
    history_sha256: str
    report_sha256: str


def read_forex_opening_statement(text, *, trade_history, transaction_report, period_end):
    """Require independent early daily positions and all monthly/history fills.

    Supported coverage uses the last weekday of the preceding/selected month.
    There must be no selected-month executions before the first daily close.
    Account identity is compared to the Forex CSV, not the securities account.
    """
    period_start = period_end.replace(day=1)
    if (period_end + timedelta(days=1)).day != 1:
        raise ValueError('Forex opening statement requires a complete month.')
    previous = period_start - timedelta(days=1)
    while previous.weekday() >= 5:
        previous -= timedelta(days=1)
    last = period_end
    while last.weekday() >= 5:
        last -= timedelta(days=1)
    pages = [p for p in text.split('\f') if p.strip()]
    if len(pages) < 2 or 'Monthly Equity Summary' not in pages[0]:
        raise ValueError('Monthly summary and first daily Forex statement are required.')
    if _coverage(pages[0]) != (previous, last):
        raise ValueError('Forex statement coverage differs from selected month.')
    first_end = period_start
    while first_end.weekday() >= 5:
        first_end += timedelta(days=1)
    if _coverage(pages[1]) != (previous, first_end):
        raise ValueError('First daily Forex statement does not establish the opening boundary.')
    # Bind the Forex account without conflating it with the TOS securities ID.
    account_area = pages[0].split('Account Currency')
    accounts = re.findall(r'\b(\d{6,})\s+USD\b', '\n'.join(account_area[1].splitlines()[:2])) if len(account_area) == 2 else []
    report_bytes = Path(transaction_report).read_bytes()
    report_accounts = re.findall(r'Account \(USD\):\s*(\d+)', report_bytes.decode('utf-8-sig'))
    if len(accounts) != 1 or accounts != report_accounts:
        raise ValueError('Forex statement and transaction report accounts differ or are ambiguous.')
    daily = pages[1]
    if daily.count('Open Position(s)') != 1 or daily.count('Closed Position(s)') != 1:
        raise ValueError('First daily Forex positions are missing or ambiguous.')
    opening_section = daily.split('Open Position(s)')[1].split('Closed Position(s)')[0]
    opening = _positions(opening_section)
    # Confirm explicit pre-period position details, never infer them from closes.
    detail_dates = re.findall(r'TR Details:\s*(\d{1,2}/\d{1,2}/\d{4})(?=\s)', opening_section)
    if (opening and not detail_dates) or len(detail_dates) != opening_section.count('TR Details:'):
        raise ValueError('Explicit Forex opening position details are required.')
    if any(datetime.strptime(v, '%m/%d/%Y').date() >= period_start for v in detail_dates):
        raise ValueError('First daily position was opened after the opening boundary.')
    if pages[0].count('Open Position(s) at End of Month') != 1:
        raise ValueError('Forex monthly closing positions are missing or ambiguous.')
    closing_section = pages[0].split('Open Position(s) at End of Month')[1]
    if 'Floating Profit/Loss Net USD' not in closing_section:
        raise ValueError('Incomplete Forex month-end position section.')
    closing = _positions(closing_section)
    history_bytes = Path(trade_history).read_bytes()
    rows = read_forex_trades(ThinkorswimSourceReader().read(str(trade_history)).section('Forex Statements'))
    # A wider history would seed the resolver twice; require monthly-only fills.
    if any(not period_start <= r.executed_at.date() <= period_end for r in rows):
        raise ValueError('Forex opening statement requires monthly-only trade history.')
    if any(r.executed_at.date() <= first_end for r in rows):
        raise ValueError('First daily positions follow executions; independent opening evidence is required.')
    history = {}
    for r in rows:
        if r.reference in history:
            raise ValueError('Duplicate history Forex execution reference.')
        history[r.reference] = (r.executed_at.date().isoformat(), r.pair, r.quantity, r.price)
    details, effects = {}, {}
    inside = False
    for line in text.splitlines():
        value = line.strip()
        if value == 'Trade Activity Detail':
            inside = True
            continue
        if value.startswith(('Financing Report', 'Conversion Report', 'Customer Account Summary Report')):
            inside = False
        if not inside:
            continue
        m = _TRADE.fullmatch(line)
        if m is None:
            if re.match(r'\s*\d{4}-\d{2}-\d{2}', line):
                raise ValueError('Unrecognized monthly Forex execution row.')
            continue
        day, pair, side, effect, quantity, rate, reference, _ = m.groups()
        quantity, rate = _amount(quantity), _amount(rate)
        if (reference in details or rate <= 0 or not quantity
                or (side == 'B') != (quantity > 0)):
            raise ValueError('Duplicate or inconsistent monthly Forex execution.')
        details[reference] = (day, pair, quantity, rate)
        effects[reference] = effect
    if details != history:
        raise ValueError('Monthly Forex statement and history executions differ.')
    tied = {}
    for r in rows:
        tied.setdefault((r.executed_at, r.pair), []).append(r.reference)
    for refs in tied.values():
        if len(refs) > 1 and [ref for ref in details if ref in refs] != refs:
            raise ValueError('Equal-time Forex execution order is ambiguous.')
    from campaigniq.importers.thinkorswim.forex_position_effect_resolver import ForexPositionEffectResolver
    resolver = ForexPositionEffectResolver(opening)
    positions = dict(opening)
    for r in sorted(rows, key=lambda r: r.executed_at):
        parts = resolver.resolve(pair=r.pair, quantity=r.quantity)
        if len(parts) != 1 or parts[0].position_effect.value != effects[r.reference]:
            raise ValueError('Forex opening position disagrees with explicit execution effects.')
        positions[r.pair] = positions.get(r.pair, Decimal(0)) + r.quantity
    if {k: v for k, v in positions.items() if v} != closing:
        raise ValueError('Forex statement opening, executions, and closing positions do not reconcile.')
    return ForexOpeningEvidence(tuple(sorted(opening.items())), tuple(sorted(closing.items())),
        sha256(text.encode()).hexdigest(), sha256(history_bytes).hexdigest(), sha256(report_bytes).hexdigest())


def attach_forex_opening(book, evidence, *, period_start, authoritative):
    """Clone bootstrap state; never replace authoritative quantities or lot IDs."""
    existing = {i.symbol: sum((lot.quantity for lot in book.lots(i)), Decimal(0))
                for i in book.instruments() if isinstance(i, ForexPair)}
    opening = dict(evidence.opening)
    if authoritative or existing:
        if existing != opening:
            raise ValueError('Forex statement opening differs from existing opening state.')
        return book.clone()
    result = book.clone()
    for pair, quantity in evidence.opening:
        result.seed(Lot(lot_id=f'SCHWAB-FOREX-OPENING:{period_start}:{pair}:{evidence.source_sha256}',
            instrument=ForexPair.from_symbol(pair), quantity=quantity,
            opened_at=datetime.combine(period_start-timedelta(days=1), datetime.max.time()),
            basis_total=None,
            basis_source=f'schwab_forex_opening_statement:{evidence.source_sha256}'))
    return result


def verify_forex_ending(book, evidence):
    positions = {i.symbol: sum((lot.quantity for lot in book.lots(i)), Decimal(0))
                 for i in book.instruments() if isinstance(i, ForexPair)}
    if {k: v for k, v in positions.items() if v} != dict(evidence.closing):
        raise ValueError('Computed Forex inventory differs from monthly statement closing positions.')
