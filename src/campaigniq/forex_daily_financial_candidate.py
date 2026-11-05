"""Exact October daily-session financial proposal; no source-policy activation.

The original CSV controls remain original evidence. This module returns a
separate calculation, never a repaired importer result or a financial write.
"""
from collections import defaultdict
import csv
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
import io
from pathlib import Path
import re

from campaigniq.forex_cash_ledger_preview import _ledger, money
from campaigniq.forex_daily_cash_resolution import _pairs
from campaigniq.forex_daily_inventory import read_daily, validate_daily_chain, amount
from campaigniq.importers.schwab.forex_transaction_reader import read_forex_transaction_report, usd
from campaigniq.importers.thinkorswim.forex_trade_reader import read_forex_trades
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader

POLICY = 'schwab_daily_sessions_october_2026_candidate_v1'
START, END = date(2026, 10, 1), date(2026, 10, 31)
ZERO = Decimal(0)


def canonical(value):
    def encode(item):
        if isinstance(item, Decimal): return str(item)
        if isinstance(item, (date, datetime)): return item.isoformat()
        raise TypeError(type(item).__name__)
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=encode, allow_nan=False)


def _reference(value): return value.strip().lstrip('=').strip('"')


def _finance_rows(statement):
    result = {}
    for row in statement.section('Forex Statements').rows():
        if len(row) != 10 or row[3].strip() != 'DOI': continue
        ref = _reference(row[4])
        if not ref.isdigit() or ref in result:
            raise ValueError('Missing or duplicate cash financing reference')
        when = datetime.strptime(row[1]+' '+row[2], '%m/%d/%y %H:%M:%S')
        if when.date() > END: continue
        result[ref] = dict(when=when, amount=money(row[8]), description=row[5])
    return result


def _securities_cash(statement):
    sections = [s for s in statement.sections if s.name == 'Cash Balance']
    if not sections: return {}
    if len(sections) != 1: raise ValueError('Ambiguous securities cash section')
    section = sections[0]; header = section.header()
    if not all(name in header for name in ('REF #', 'AMOUNT', 'DESCRIPTION')):
        raise ValueError('Unsupported securities cash reference columns')
    result = defaultdict(list)
    for row in section.rows():
        if len(row) != len(header) or row == header: continue
        ref = _reference(row[header.index('REF #')])
        if ref.isdigit():
            result[ref].append(dict(amount=money(row[header.index('AMOUNT')]),
                description=row[header.index('DESCRIPTION')]))
    return result


def calculate(reports, *, known, cash_ledger, transaction_report, opening_book=None):
    paths = [Path(cash_ledger), Path(transaction_report)]
    originals = [p.read_bytes() for p in paths]
    ledger = _ledger(paths[0], END)
    if ledger['differences'] or any(row[3].strip()=='SETL' and money(row[8]) for _,row,_ in ledger['selected']):
        raise ValueError('Balanced cash ledger without unexplained settlement movements required')
    statement = ThinkorswimSourceReader().read(str(paths[0]))
    trades = [r for r in read_forex_trades(statement.section('Forex Statements'))
        if START <= r.executed_at.date() <= END]
    inventory = validate_daily_chain(reports, known=known, period_start=START, period_end=END,
        opening_book=opening_book, history_rows=trades)
    if inventory['last_daily_closing_quantities']:
        raise ValueError('This October candidate requires a flat final daily position control')
    last = date.fromisoformat(inventory['last_independent_daily_boundary'])
    if (ledger['closing'] != Decimal(inventory['last_daily_cash_usd']) or
        any(row[3].strip() == 'TRD' or money(row[8]) for when, row, ref in ledger['selected'] if when.date() > last)):
        raise ValueError('Later cash-export activity or balance differs from the last daily evidence')
    parsed = []
    for source in reports:
        daily = read_daily(source['text'], known=known)
        daily.update(text=source['text'].replace('\f', '\n').split('Forex Customer Statement Disclosures', 1)[0],
            pdf_sha256=source['pdf_sha256'])
        parsed.append(daily)
    parsed.sort(key=lambda r: r['end'])
    raw = read_forex_transaction_report(paths[1])
    for fields in csv.reader(io.StringIO(originals[1].decode('utf-8-sig'))):
        if len(fields)>3 and fields[3].strip().lower()=='financing':
            if len(fields)!=15 or not fields[13].strip() or not fields[14].strip() or usd(fields[13])!=usd(fields[14]):
                raise ValueError('Original financing amount and delivery amount differ or are unsupported')
    if (raw.period_start, raw.period_end) != (date(2026,9,30), END):
        raise ValueError('Exact original October transaction report required')
    if (raw.mtd_fee_usd != 0 or raw.commission_total_usd not in (None, ZERO) or
        any(r.fee_usd for r in (*raw.new_transactions,*raw.settlements)) or
        any(row[6].strip() not in ('', '--', '0', '0.00', '$0.00') for _, row, _ in ledger['selected'])):
        raise ValueError('Nonzero or unsupported separate fees require an explicit policy')
    events = {r.order_id: r for r in (*raw.new_transactions, *raw.settlements)}
    if len(events) != len(raw.new_transactions)+len(raw.settlements):
        raise ValueError('Duplicate original transaction reference')
    cash_trades = {r.reference: r for r in trades}
    if set(events)-set(cash_trades): raise ValueError('CSV execution absent from daily/cash evidence')
    for ref, event in events.items():
        trade = cash_trades[ref]
        if (event.instrument, event.amount, event.rate, event.side.upper()) != (
            trade.pair, trade.quantity, trade.price, 'BUY' if trade.quantity > 0 else 'SELL'):
            raise ValueError('Original CSV execution facts differ')
    cash_finance = _finance_rows(statement)
    daily_finance = {}; closes = []; previous_pending = {}; bridges = []
    positions = {k: Decimal(v) for k,v in inventory['statement_opening_quantities'].items()}
    original_closes = {r.order_id: r for r in raw.settlements}
    for daily in parsed:
        controls = daily['controls']; text = daily['text']
        if any(controls[k] for k in ('Deposits', 'Withdrawals', 'Adjustments')):
            raise ValueError('Daily deposits, withdrawals or adjustments require separate treatment')
        table = _pairs(text.split('Profit/Loss Summary', 1)[1].split('Open Position(s)', 1)[0], 3)
        closed = _pairs(text.split('Closed Position(s)', 1)[1].split('Trade Activity Detail', 1)[0], 1)
        for index, label in enumerate(('Settled Profit/Loss', 'Unrealized Profit/Loss', 'Floating Profit/Loss')):
            if sum((v[index] for v in table.values()), ZERO) != controls[label]:
                raise ValueError('Daily per-pair '+label+' differs from summary')
        pending = {k:v[1] for k,v in table.items()}
        if {k:v[0] for k,v in closed.items() if v[0]} != {k:v for k,v in pending.items() if v}:
            raise ValueError('Daily closed-position dollars differ from pending controls')
        if daily is parsed[0] and (any(pending.values()) or controls['Settled Profit/Loss']):
            raise ValueError('Opening pending/settled dollars require prior-period evidence')
        groups = defaultdict(list)
        for fill in sorted(daily['fills'], key=lambda r:r['when']):
            if fill['effect'] not in ('OPEN', 'CLOSE'):
                raise ValueError('Split close/open execution requires separate allocation review')
            positions[fill['pair']] = positions.get(fill['pair'], ZERO)+fill['quantity']
            old = events.get(fill['reference'])
            if old is not None and old.total_position != positions[fill['pair']]:
                raise ValueError('Explicit CSV Total Position contradicts daily replay')
            if fill['effect'] == 'CLOSE': groups[fill['pair']].append(fill)
            elif old is None or fill['reference'] in original_closes:
                raise ValueError('Opening fill requires its original CSV financial record')
        for pair in sorted(set(table)|set(previous_pending)|set(groups)):
            settled = table.get(pair, (ZERO, ZERO, ZERO))[0]
            prior = previous_pending.get(pair, ZERO); current = pending.get(pair, ZERO)
            new_profit = settled-prior+current
            group = groups.get(pair, [])
            if len(group) > 1:
                raise ValueError('Multiple same-pair daily closes require an independently supported dollar allocation')
            if not group and new_profit:
                raise ValueError('Daily settlement bridge has dollars without a new close')
            if group:
                fill = group[0]; trade = cash_trades[fill['reference']]
                if trade.broker_pnl_usd is None:
                    raise ValueError('Cash close lacks an explicit USD amount')
                old = original_closes.get(fill['reference'])
                if fill['reference'] in events and old is None:
                    raise ValueError('CSV position effect differs from daily close')
                closes.append(dict(reference=fill['reference'], pair=pair, quantity=str(fill['quantity']),
                    rate=str(fill['rate']), daily_observed_at=fill['when'].isoformat(),
                    cash_observed_at=trade.executed_at.isoformat(), reporting_boundary=str(daily['end']),
                    csv_trade_at=None if old is None else old.trade_at.isoformat(),
                    csv_settlement_at=None if old is None or old.settlement_at is None else old.settlement_at.isoformat(),
                    candidate_pl_usd=str(new_profit), cash_export_pl_usd=str(trade.broker_pnl_usd),
                    csv_pl_usd=None if old is None else str(old.settlement_pl_usd),
                    candidate_minus_cash_usd=str(new_profit-trade.broker_pnl_usd)))
            bridges.append(dict(boundary=str(daily['end']),pair=pair,settled_usd=str(settled),
                prior_pending_usd=str(prior),pending_usd=str(current),new_close_pl_usd=str(new_profit)))
        previous_pending = pending
        section = text.split('Financing Report', 1)[1].split('Conversion Report', 1)[0]
        for line in section.splitlines():
            if not re.match(r'\s*\d{4}-\d\d-\d\d', line): continue
            parts = line.split(); ref = parts[5]
            if ref in daily_finance: raise ValueError('Duplicate daily financing reference')
            value = amount(parts[7]); cash = cash_finance.get(ref)
            if cash is None or cash['amount'] != value:
                raise ValueError('Daily financing reference/amount differs from cash export')
            # If the cash description names a pair it must agree. Some older
            # exports say only Interest charged; that absence stays explicit.
            pairs = re.findall(r'\b[A-Z]{3}/[A-Z]{3}\b', cash['description'])
            if pairs and pairs != [parts[2]]: raise ValueError('Financing pair differs between sources')
            daily_finance[ref] = dict(reference=ref,pair=parts[2],amount_usd=str(value),
                daily_observed_at=' '.join(parts[:2]),cash_observed_at=cash['when'].isoformat(),
                reporting_boundary=str(daily['end']),cash_pair_explicit=bool(pairs))
    if any(previous_pending.values()):
        raise ValueError('Final pending settlements require a separate month-boundary policy')
    calendar_ids = {ref for when, row, ref in ledger['selected'] if row[3].strip() == 'DOI'}
    matched_calendar = {ref for ref in daily_finance if START <= cash_finance[ref]['when'].date() <= END}
    if calendar_ids != matched_calendar:
        raise ValueError('Daily/calendar financing reference sets differ')
    prior_finance = sum((Decimal(v['amount_usd']) for k,v in daily_finance.items() if k not in calendar_ids), ZERO)
    if any(cash_finance[k]['when'].date() != date(2026,9,30) for k in daily_finance if k not in calendar_ids):
        raise ValueError('Unexplained financing outside the September/October boundary')
    financing = sum((Decimal(v['amount_usd']) for v in daily_finance.values()), ZERO)
    trading = sum((Decimal(v['candidate_pl_usd']) for v in closes), ZERO)
    opening = Decimal(inventory['opening_cash_usd']); ending = ledger['closing']
    calendar_financing = ledger['totals'].get('DOI', ZERO)
    calendar_trading = ledger['totals'].get('TRD', ZERO)
    opening_delta = opening+prior_finance-ledger['opening']
    close_delta = sum((Decimal(v['candidate_minus_cash_usd']) for v in closes), ZERO)
    checks = dict(daily_cash=ending-opening-trading-financing,
        daily_settled=trading-Decimal(inventory['daily_settled_pl_usd']),
        daily_financing=financing-Decimal(inventory['daily_financing_usd']),
        calendar_cash=ending-ledger['opening']-calendar_trading-calendar_financing,
        financing_cutoff=financing-prior_finance-calendar_financing,
        cash_close_total=calendar_trading-sum((Decimal(v['cash_export_pl_usd']) for v in closes), ZERO),
        opening_and_close_differences=opening_delta+close_delta)
    if any(checks.values()): raise ValueError('Exact daily/calendar financial equations do not reconcile')
    securities = _securities_cash(statement); classifications = []
    raw_finance_ids = set()
    for row in raw.financing:
        if row.order_id in raw_finance_ids: raise ValueError('Duplicate original CSV financing reference')
        raw_finance_ids.add(row.order_id)
        daily = daily_finance.get(row.order_id)
        if daily:
            if row.instrument not in ('USD$FX',daily['pair']) or row.financing_usd != Decimal(daily['amount_usd']):
                raise ValueError('Original CSV financing amount contradicts independent daily detail')
            classification = 'matched_daily_forex_financing'
        else:
            matches = [r for r in securities.get(row.order_id, []) if r['amount'] == row.financing_usd]
            classification = ('matched_securities_cash_reference_and_amount'
                if row.instrument == 'US$' and len(matches) == 1 else 'unclassified_original_csv_financing')
        classifications.append(dict(reference=row.order_id,instrument=row.instrument,
            original_usd=str(row.financing_usd),classification=classification))
    missing = sorted(set(cash_trades)-set(events))
    if set(missing)-{v['reference'] for v in closes}:
        raise ValueError('Missing CSV execution is not a uniquely matched close')
    if originals != [p.read_bytes() for p in paths]: raise ValueError('Financial originals changed during calculation')
    allocations = defaultdict(Decimal)
    for row in inventory['preview_close_allocations']:
        allocations[row['reference']] += abs(Decimal(row['quantity']))
    if opening_book is not None and (set(allocations) != {r['reference'] for r in closes} or
        any(allocations[r['reference']] != abs(Decimal(r['quantity'])) for r in closes)):
        raise ValueError('Candidate closes differ from exact published-lot allocations')
    body = dict(format='campaigniq.forex_daily_financial_candidate',version=1,policy_id=POLICY,
        period_start=str(START),period_end=str(END),
        basis='Daily reporting sessions; exact close-day pending-to-settled bridge.',
        totals=dict(opening_cash_usd=str(opening),trading_usd=str(trading),financing_usd=str(financing),
            ending_cash_usd=str(ending),net_cash_change_usd=str(trading+financing)),
        calendar_comparison=dict(original_opening_cash_usd=str(ledger['opening']),
            original_trade_cash_usd=str(calendar_trading),original_financing_usd=str(calendar_financing),
            prior_calendar_financing_in_daily_sessions_usd=str(prior_finance),
            daily_derived_calendar_opening_usd=str(opening+prior_finance),
            derived_opening_minus_export_usd=str(opening_delta),daily_closes_minus_export_usd=str(close_delta)),
        closes=closes,financing=list(daily_finance.values()),settlement_bridges=bridges,
        raw_csv_controls=json.loads(canonical(asdict(raw))),
        raw_csv_financing_classification=classifications,missing_csv_execution_references=missing,
        daily_financing_absent_from_csv=sorted(set(daily_finance)-raw_finance_ids),
        original_csv_mtd_minus_candidate_usd=str(raw.mtd_settled_pl_usd-trading),
        all_original_financing_rows_classified=all(r['classification']!='unclassified_original_csv_financing' for r in classifications),
        all_exact_financial_equations_verified=True,equation_deltas_usd={k:str(v) for k,v in checks.items()},
        authoritative_opening_matches=inventory['authoritative_opening_matches'],
        close_allocations=inventory['preview_close_allocations'],
        execution_clock_observations=inventory['execution_clock_observations'],
        ending_quantities=inventory['last_daily_closing_quantities'],
        last_independent_daily_boundary=inventory['last_independent_daily_boundary'],
        full_independent_daily_coverage=inventory['full_independent_daily_coverage'],
        dates_without_supplied_daily_reports=inventory['dates_without_supplied_daily_reports'],
        later_cash_export_unchanged=True,
        source_sha256=dict(cash_ledger=sha256(originals[0]).hexdigest(),transaction_report=sha256(originals[1]).hexdigest(),
            daily_pdfs=sorted(r['pdf_sha256'] for r in reports)),
        original_discrepancies_resolved=False,policy_approved=False,financial_state_written=False,publication_authorized=False,
        limitations=['Candidate calculation only; daily-source account/coverage confirmation is not financial-policy approval.',
            'Independent daily confirmation stops at its stated boundary; later unchanged export evidence is separate.',
            'Original CSV controls and monetary differences remain retained; no original was repaired or reclassified in storage.',
            'Publication writer and durable financial-policy approval are not enabled by this proposal.'])
    body['candidate_sha256'] = sha256(canonical(body).encode()).hexdigest()
    return body
