"""Independent daily Forex position/cash chain, with explicit coverage boundaries."""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re

from campaigniq.account_forex_inventory_preview import _positions_checked
from campaigniq.broker_account_binding import BrokerBinding
from campaigniq.forex_opening_statement import _coverage, _TRADE, _amount, ForexOpeningEvidence, attach_forex_opening
from campaigniq.importers.thinkorswim.forex_position_effect_resolver import ForexPositionEffectResolver
from campaigniq.importers.thinkorswim.forex_trade_row import ThinkorswimForexTradeRow
from campaigniq.importers.thinkorswim.forex_translator import to_forex_trade
from campaigniq.persistence.lot_book_store import serialize_lot_book

ZERO = Decimal(0)
LABELS = ('Beginning Cash Balance', 'Ending Cash Balance', 'Beginning Account Equity', 'Ending Account Equity',
          'Deposits', 'Withdrawals', 'Adjustments', 'Financing', 'Settled Profit/Loss', 'Unrealized Profit/Loss', 'Floating Profit/Loss')


def amount(value):
    try:
        return _amount(value.replace('$', ''))
    except (ValueError, InvalidOperation) as error:
        raise ValueError('Invalid daily Forex amount') from error


def read_daily(text, *, known):
    clean = text.replace('\f', '\n').strip()
    if clean.count('Customer Account Summary Report since ') != 1:
        raise ValueError('Each PDF must contain exactly one dated daily Forex report')
    clean = clean.split('Forex Customer Statement Disclosures', 1)[0].strip()
    start, end = _coverage(clean)
    next_day = start + timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)
    if start >= end or end != next_day:
        raise ValueError('Unsupported daily Forex coverage')
    # Daily layouts print the cash table to the left of the account panel.
    # Bind only the identifier under the explicit Account/Account Currency header.
    lines = clean.splitlines()
    observed = []
    for index, line in enumerate(lines):
        header = re.search(r'\bAccount[ \t]+Account Currency[ \t]*$', line)
        if header:
            if index+1 >= len(lines):
                raise ValueError('Missing daily Forex account row')
            account = re.fullmatch(r'\s*([0-9]{6,12})\s+USD\s*', lines[index+1][header.start():])
            if account is None:
                raise ValueError('Daily account panel is missing, masked or ambiguous')
            observed.append(BrokerBinding.create('forex', account[1]))
    if len(observed) != 1 or observed[0] != known.get('forex'):
        raise ValueError('Daily PDF belongs to a different or unverified Forex account')
    if 'Monthly Equity Summary' in clean or clean.count('Equity Summary') != 1:
        raise ValueError('A daily Forex report is required')
    summary = clean.split('Profit/Loss Summary', 1)[0]
    values = {}
    for label in LABELS:
        matches = re.findall(r'^\s*' + re.escape(label) + r'\s+(\(?\$[\d,.]+\)?)', summary, re.M)
        if len(matches) != 1:
            raise ValueError('Missing or ambiguous daily control: ' + label)
        values[label] = amount(matches[0])
    cash_delta = (values['Ending Cash Balance'] - values['Beginning Cash Balance'] - values['Deposits']
        + values['Withdrawals'] - values['Adjustments'] - values['Financing'] - values['Settled Profit/Loss'])
    equity_delta = values['Ending Account Equity'] - values['Ending Cash Balance'] - values['Unrealized Profit/Loss'] - values['Floating Profit/Loss']
    if cash_delta or equity_delta:
        raise ValueError('Daily Forex cash or equity equation does not reconcile')
    for section in ('Open Position(s)', 'Closed Position(s)', 'Trade Activity Detail', 'Financing Report', 'Conversion Report'):
        if clean.count(section) != 1:
            raise ValueError('Missing or ambiguous daily section: ' + section)
    open_section = clean.split('Open Position(s)', 1)[1].split('Closed Position(s)', 1)[0]
    positions = _positions_checked(open_section, earliest=date.min, latest=end)
    trade_section = clean.split('Trade Activity Detail', 1)[1].split('Financing Report', 1)[0]
    fills = []
    for line in trade_section.splitlines():
        if not re.match(r'\s*\d{4}-\d\d-\d\d', line):
            continue
        match = _TRADE.fullmatch(line)
        if match is None:
            raise ValueError('Unsupported daily Forex execution row')
        day, pair, side, effect, number, price, reference, markup = match.groups()
        when = datetime.fromisoformat(' '.join(line.split()[:2]))
        quantity, rate = amount(number), amount(price)
        if not start <= when.date() <= end or not quantity or rate <= 0 or (side == 'B') != (quantity > 0):
            raise ValueError('Daily execution coverage, quantity, side or rate differs')
        fills.append(dict(reference=reference, when=when, pair=pair, quantity=quantity, rate=rate, effect=effect))
    financing = []
    section = clean.split('Financing Report', 1)[1].split('Conversion Report', 1)[0]
    for line in section.splitlines():
        if not re.match(r'\s*\d{4}-\d\d-\d\d', line):
            continue
        parts = line.split()
        if len(parts) != 8 or not parts[5].isdigit() or not re.fullmatch('[A-Z]{3}/[A-Z]{3}', parts[2]):
            raise ValueError('Unsupported daily financing row')
        when = datetime.fromisoformat(' '.join(parts[:2]))
        if not start <= when.date() <= end or not amount(parts[3]) or amount(parts[4]) <= 0 or amount(parts[6]) <= 0:
            raise ValueError('Invalid daily financing coverage or units')
        financing.append(dict(reference=parts[5], amount=amount(parts[7])))
    if sum((row['amount'] for row in financing), ZERO) != values['Financing']:
        raise ValueError('Daily financing detail differs from its summary')
    return dict(start=start, end=end, controls=values, positions=positions, fills=fills,
                financing=financing, open_section=open_section, text_sha256=sha256(text.encode()).hexdigest())


def validate_daily_chain(reports, *, known, period_start, period_end, opening_book=None, history_rows=None):
    """Reports contain label/text/original-PDF fingerprint; originals stay unchanged."""
    if (period_start, period_end) != (date(2026, 10, 1), date(2026, 10, 31)):
        raise ValueError('Daily Forex preview currently supports October 2026 only')
    if not reports:
        raise ValueError('Daily Forex reports are required')
    parsed = []
    for source in reports:
        row = read_daily(source['text'], known=known)
        row.update(name=source['name'], pdf_sha256=source['pdf_sha256'])
        parsed.append(row)
    parsed.sort(key=lambda row: row['end'])
    expected = period_start - timedelta(days=1)
    if parsed[0]['start'] != expected:
        raise ValueError('First daily report must start at the September closing boundary')
    previous_cash = previous_equity = None
    references, finance_ids, source_hashes = set(), set(), set()
    for row in parsed:
        if row['start'] != expected or row['end'] > period_end or row['pdf_sha256'] in source_hashes:
            raise ValueError('Daily reports have a coverage gap, overlap, duplicate or wrong month')
        expected = row['end']; source_hashes.add(row['pdf_sha256'])
        values = row['controls']
        if previous_cash is not None and (values['Beginning Cash Balance'] != previous_cash or values['Beginning Account Equity'] != previous_equity):
            raise ValueError('Successive daily cash/equity boundaries differ')
        previous_cash, previous_equity = values['Ending Cash Balance'], values['Ending Account Equity']
        for fill in row['fills']:
            if fill['reference'] in references:
                raise ValueError('Duplicate daily execution reference')
            references.add(fill['reference'])
        for item in row['financing']:
            if item['reference'] in finance_ids:
                raise ValueError('Duplicate daily financing reference')
            finance_ids.add(item['reference'])
    first = parsed[0]
    if first['fills']:
        raise ValueError('First daily report follows an execution; separate opening evidence is required')
    opening = _positions_checked(first['open_section'], earliest=date.min, latest=period_start-timedelta(days=1))
    clone = None
    initial_text = None
    if opening_book is not None:
        proof = ForexOpeningEvidence(tuple(sorted(opening.items())), (), 'daily', 'daily', 'daily', period_end, ())
        initial_text = serialize_lot_book(period_end=period_start-timedelta(days=1), lot_book=opening_book)
        clone = attach_forex_opening(opening_book, proof, period_start=period_start, authoritative=True)
    resolver = ForexPositionEffectResolver(opening)
    positions = dict(opening)
    allocations = []
    all_fills = []
    checks = []
    for report in parsed:
        for fill in sorted(report['fills'], key=lambda item: item['when']):
            parts = resolver.resolve(pair=fill['pair'], quantity=fill['quantity'])
            if '+'.join(part.position_effect.value for part in parts) != fill['effect']:
                raise ValueError('Daily explicit position effect differs from carried inventory')
            positions[fill['pair']] = positions.get(fill['pair'], ZERO) + fill['quantity']
            if clone is not None:
                row = ThinkorswimForexTradeRow(fill['when'], fill['reference'], 'BOT' if fill['quantity'] > 0 else 'SOLD',
                    fill['quantity'], fill['pair'], fill['rate'], None)
                for part in parts:
                    for allocation in clone.apply_trade(to_forex_trade(row, part.position_effect, part.quantity)):
                        allocations.append(dict(reference=fill['reference'], lot_id=allocation.lot_id,
                            campaign_id=allocation.campaign_id, quantity=str(allocation.quantity)))
            all_fills.append(fill)
        positions = {pair: quantity for pair, quantity in positions.items() if quantity}
        if positions != report['positions']:
            raise ValueError('Replayed inventory differs from an independent daily closing position table')
        checks.append(dict(period_start=str(report['start']), period_end=str(report['end']),
            cash_begin=str(report['controls']['Beginning Cash Balance']), cash_end=str(report['controls']['Ending Cash Balance']),
            cash_equation_verified=True, equity_equation_verified=True, inventory_reconciled=True,
            positions={pair: str(q) for pair, q in sorted(report['positions'].items())},
            pdf_sha256=report['pdf_sha256'], extracted_text_sha256=report['text_sha256']))
    execution_matches = False
    clock_differences = []
    if history_rows is not None:
        history = {row.reference: row for row in history_rows}
        if len(history) != len(history_rows) or set(history) != references:
            raise ValueError('Daily and saved cash execution reference sets differ')
        for fill in all_fills:
            row = history[fill['reference']]
            if (row.pair, row.quantity, row.price) != (fill['pair'], fill['quantity'], fill['rate']):
                raise ValueError('Daily and saved cash execution units, pair or price differ')
            clock_differences.append(dict(reference=fill['reference'], daily_time=fill['when'].isoformat(),
                cash_time=row.executed_at.isoformat(), observed_seconds=int((fill['when']-row.executed_at).total_seconds())))
        execution_matches = True
    if opening_book is not None and serialize_lot_book(period_end=period_start-timedelta(days=1), lot_book=opening_book) != initial_text:
        raise ValueError('Daily inventory review mutated authoritative opening state')
    last_business_day = period_end
    while last_business_day.weekday() >= 5:
        last_business_day -= timedelta(days=1)
    dates = []
    cursor = parsed[-1]['end'] + timedelta(days=1)
    while cursor <= last_business_day:
        if cursor.weekday() < 5: dates.append(str(cursor))
        cursor += timedelta(days=1)
    return dict(format='campaigniq.forex_daily_inventory_preview', version=1,
        daily_report_count=len(parsed), first_boundary=str(first['start']), last_independent_daily_boundary=str(parsed[-1]['end']),
        full_independent_daily_coverage=not dates, dates_without_supplied_daily_reports=dates,
        statement_opening_quantities={pair: str(q) for pair, q in sorted(opening.items())},
        last_daily_closing_quantities={pair: str(q) for pair, q in sorted(positions.items())},
        opening_cash_usd=str(first['controls']['Beginning Cash Balance']), last_daily_cash_usd=str(previous_cash),
        daily_settled_pl_usd=str(sum((r['controls']['Settled Profit/Loss'] for r in parsed), ZERO)),
        daily_financing_usd=str(sum((r['controls']['Financing'] for r in parsed), ZERO)),
        daily_inventory_chain_verified=True, daily_cash_chain_verified=True,
        authoritative_opening_matches=opening_book is not None,
        authoritative_replay_matches_each_daily_close=opening_book is not None,
        saved_execution_facts_verified=execution_matches, execution_count=len(all_fills),
        execution_clock_observations=clock_differences, preview_close_allocations=allocations, daily_checks=checks,
        financial_state_written=False, publication_authorized=False,
        limitation='Daily confirmation stops at its stated boundary. Later unchanged cash-export rows remain separate evidence; no missing daily report is invented.')
