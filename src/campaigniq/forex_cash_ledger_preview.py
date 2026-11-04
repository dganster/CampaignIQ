"""Read-only Forex cash evidence preview; never authorizes publication.

The balanced cash ledger is a candidate monetary source. Source conflicts are
reported, not repaired. No existing importer or publication policy is changed.
"""
from collections import Counter
import csv
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import io
from pathlib import Path

from campaigniq.importers.thinkorswim.forex_trade_reader import read_forex_trades
from campaigniq.importers.schwab.forex_transaction_reader import read_forex_transaction_report
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader

ZERO = Decimal(0)


def money(value):
    value = value.strip().replace('$', '').replace(',', '')
    if value in ('', '--'):
        return ZERO
    result = -Decimal(value[1:-1]) if value.startswith('(') and value.endswith(')') else Decimal(value)
    if not result.is_finite():
        raise ValueError('Non-finite monetary amount')
    return result


def _ledger(path, end):
    statement = ThinkorswimSourceReader().read(str(path))
    sections = [s for s in statement.sections if s.name == 'Forex Statements']
    if len(sections) != 1:
        raise ValueError('One Forex cash section required')
    section = sections[0]
    expected = ['', 'Date', 'Time', 'Type', 'Ref #', 'Description', 'Commissions & Fees', 'Amount', 'Amount(USD)', 'Balance']
    if section.header() != expected:
        raise ValueError('Unsupported Forex cash columns')
    start = end.replace(day=1)
    previous = None
    previous_time = None
    opening = closing = None
    by_type = Counter()
    seen = set()
    differences = []
    selected = []
    last_day = None
    for row in section.rows():
        if row == expected or not any(row):
            continue
        if len(row) != len(expected):
            raise ValueError('Malformed Forex cash row')
        try:
            instant = datetime.strptime(row[1] + ' ' + row[2], '%m/%d/%y %H:%M:%S')
        except ValueError:
            # The known aggregate footer has no dated entry.
            if row[5].strip() == 'TOTAL' or row[4].strip() == 'TOTAL':
                continue
            raise ValueError('Undated Forex cash row')
        if previous_time is not None and instant < previous_time:
            raise ValueError('Unordered Forex cash ledger')
        previous_time = instant
        day = instant.date()
        if day > end:
            break
        balance = money(row[9])
        if start <= day:
            if previous is None:
                raise ValueError('Pre-month cash boundary required')
            if opening is None:
                opening = previous
            typ = row[3].strip()
            if typ not in ('BAL', 'TRD', 'DOI', 'SETL'):
                raise ValueError('Unsupported cash movement: ' + typ)
            if money(row[6]):
                raise ValueError('Separate fees require explicit treatment')
            amount = money(row[8])
            if typ != 'BAL':
                ref = row[4].strip().lstrip('=').strip('"')
                if ref not in ('', '--'):
                    if ref in seen:
                        raise ValueError('Duplicate cash reference')
                    seen.add(ref)
                by_type[typ] += amount
                selected.append((instant, row, ref))
            if balance - previous != (ZERO if typ == 'BAL' else amount):
                differences.append(dict(date=str(day), reference=row[4], reported=amount, balance_change=balance-previous))
            closing = balance
            last_day = day
        previous = balance
    if opening is None or closing is None or last_day != end:
        raise ValueError('Complete selected-month cash coverage required')
    trades = [r for r in read_forex_trades(section) if start <= r.executed_at.date() <= end]
    return dict(opening=opening, closing=closing, totals=dict(by_type), differences=differences,
                trades=trades, selected=selected)


def _execution_facts(path, end):
    statement = ThinkorswimSourceReader().read(str(path))
    sections = [s for s in statement.sections if s.name == 'Account Trade History' or s.name.startswith('Account Trade History filtered by ')]
    if len(sections) != 1:
        raise ValueError('One full Account Trade History section required')
    filtered = sections[0].name.removeprefix('Account Trade History filtered by ') if sections[0].name != 'Account Trade History' else None
    result = []
    for row in csv.DictReader(io.StringIO('\n'.join(sections[0].lines))):
        if row.get('Type') != 'FOREX':
            continue
        if filtered is not None and row.get('Symbol') != filtered:
            raise ValueError('Filtered execution export contains another pair')
        when = datetime.strptime(row['Exec Time'], '%m/%d/%y %H:%M:%S')
        if end.replace(day=1) <= when.date() <= end:
            qty = Decimal(row['Qty'].replace(',', ''))
            price = Decimal(row['Price'])
            if not qty.is_finite() or not price.is_finite() or not qty or price <= 0:
                raise ValueError('Invalid Forex execution')
            if row['Side'] != ('BUY' if qty > 0 else 'SELL'):
                raise ValueError('Execution side disagrees with quantity')
            result.append((when, row['Symbol'], qty, price))
    return Counter(result)


def preview(*, cash_ledger, trade_history, transaction_report, period_end, supplemental_history=None):
    paths = {k: Path(v) for k, v in dict(cash_ledger=cash_ledger, trade_history=trade_history, transaction_report=transaction_report).items()}
    if supplemental_history is not None:
        paths['supplemental_history'] = Path(supplemental_history)
    originals = {k: p.read_bytes() for k, p in paths.items()}
    if period_end != date(2026, 10, 31):
        raise ValueError('Development preview supports October 2026 only')
    ledger = _ledger(paths['cash_ledger'], period_end)
    if ledger['differences']:
        raise ValueError('Candidate cash ledger has row balance discrepancies')
    facts = Counter((r.executed_at, r.pair, r.quantity, r.price) for r in ledger['trades'])
    base_facts = _execution_facts(paths['trade_history'], period_end)
    supplementary = _execution_facts(paths['supplemental_history'], period_end) if supplemental_history is not None else Counter()
    if {r[1] for r in base_facts} & {r[1] for r in supplementary}:
        raise ValueError('Supplement must cover distinct pairs; overlapping evidence requires separate review')
    if not facts or facts != base_facts + supplementary:
        raise ValueError('Cash trades do not exactly match selected-month executions')
    raw = read_forex_transaction_report(paths['transaction_report'])
    if (raw.period_start, raw.period_end) != (date(2026, 9, 30), period_end):
        raise ValueError('Complete October transaction report required')
    indexed = {r.reference: r for r in ledger['trades']}
    if len(indexed) != len(ledger['trades']):
        raise ValueError('Duplicate execution reference')
    events = (*raw.new_transactions, *raw.settlements)
    if len({r.order_id for r in events}) != len(events):
        raise ValueError('Duplicate transaction report reference')
    for r in events:
        match = indexed.get(r.order_id)
        # Export clocks differ; exact reference, pair, side, units and price bind these rows.
        if match is None or (r.instrument, r.amount, r.rate, r.side.upper()) != (match.pair, match.quantity, match.price, 'BUY' if match.quantity > 0 else 'SELL'):
            raise ValueError('Transaction reference does not match execution facts')
    trading = ledger['totals'].get('TRD', ZERO)
    financing = ledger['totals'].get('DOI', ZERO)
    if ledger['totals'].get('SETL', ZERO):
        raise ValueError('Additional settlement cash requires explicit treatment')
    if ledger['closing']-ledger['opening'] != trading+financing:
        raise ValueError('Monthly cash equation does not reconcile')
    earlier = _ledger(paths['trade_history'], period_end)
    conflicts = [dict(reference=r.order_id, pair=r.instrument, csv_usd=r.settlement_pl_usd,
                      candidate_usd=indexed[r.order_id].broker_pnl_usd)
                 for r in raw.settlements if r.settlement_pl_usd != indexed[r.order_id].broker_pnl_usd]
    if any(p.read_bytes() != originals[k] for k, p in paths.items()):
        raise ValueError('Source changed during review')
    return dict(format='campaigniq.forex_cash_ledger_preview', version=1, period_end=str(period_end),
        publication_authorized=False, account_identity_verified=False, closing_inventory_verified=False,
        candidate_cash_balanced=True, executions_match=True,
        source_sha256={k: sha256(v).hexdigest() for k,v in originals.items()},
        opening_cash_usd=ledger['opening'], ending_cash_usd=ledger['closing'],
        candidate_trading_usd=trading, candidate_financing_usd=financing,
        candidate_net_cash_change_usd=trading+financing,
        csv_mtd_settled_pl_usd=raw.mtd_settled_pl_usd,
        csv_mtd_minus_candidate_usd=raw.mtd_settled_pl_usd-trading,
        supplemental_execution_count=sum(supplementary.values()),
        omitted_execution_references=sorted(set(indexed)-{r.order_id for r in events}),
        settlement_source_differences=conflicts,
        saved_history_cash_differences=earlier['differences'],
        limitations=['Read-only development candidate; no publication integration',
            'Cash/execution agreement is not independent broker verification',
            'Account identity and authoritative opening/closing inventory require separate validation',
            'CSV monetary and financing classifications remain original unresolved evidence'])
