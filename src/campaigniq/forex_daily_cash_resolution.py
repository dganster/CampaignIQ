"""Evidence-bound March/November/December 2025 Forex financial controls.

Monthly daily reports supply close-day USD amounts and pending settlements.
History supplies exact fills; CSV retains reported dates, fees and split effects.
Original sources and every monetary disagreement are retained in the audit.
"""
from collections import defaultdict
import csv
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
import io
from pathlib import Path
import re

from campaigniq.forex_opening_statement import read_forex_opening_statement, _TRADE, _amount, _coverage
from campaigniq.forex_statement_resolution import MONEY, summary
from campaigniq.importers.schwab.forex_transaction_reader import read_forex_transaction_report, SchwabForexSettlement, SchwabForexFinancing
from campaigniq.importers.thinkorswim.forex_trade_reader import read_forex_trades
from campaigniq.importers.thinkorswim.forex_position_effect_resolver import ForexPositionEffectResolver
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader

POLICY = 'schwab_daily_cash_bridge_2025_v1'
SUPPORTED = (date(2025, 3, 31), date(2025, 11, 30), date(2025, 12, 31))
ZERO = Decimal(0)


def attribute_daily_cash_settlements(campaigns, settlements, trade_history):
    """Match verified reference facts to an exact history fill, never latest time."""
    from campaigniq.domain.forex_settlement_attribution import ForexSettlementAttribution
    from campaigniq.domain.value_objects.forex_pair import ForexPair
    history = {r.reference: r for r in read_forex_trades(ThinkorswimSourceReader().read(str(trade_history)).section('Forex Statements'))}
    result = []
    for settlement in settlements:
        row = history[settlement.order_id]
        ids = {campaign.campaign_id for campaign in campaigns for trade in campaign.trades for leg in trade.legs
               if isinstance(leg.instrument, ForexPair) and leg.instrument.symbol == row.pair
               and leg.position_effect.value == 'CLOSE'
               and leg.side.value == ('BUY' if settlement.amount > 0 else 'SELL')
               for fill in leg.executions if fill.executed_at == row.executed_at
               and abs(fill.quantity) == abs(settlement.amount) and fill.execution_price == row.price}
        if len(ids) != 1:
            raise ValueError('Resolved Forex close lacks one exact history-fill campaign attribution.')
        result.append(ForexSettlementAttribution(settlement, next(iter(ids))))
    return tuple(result)


def _pairs(section, columns):
    result = {}
    for line in section.splitlines():
        if not re.match(r'^\s*[A-Z]{3}/[A-Z]{3}(?:\s|$)', line):
            continue
        pattern = r'\s*([A-Z]{3}/[A-Z]{3})' + (rf'\s+({MONEY})' * columns) + r'\s*'
        match = re.fullmatch(pattern, line)
        if match is None or match[1] in result:
            raise ValueError('Unsupported or duplicate daily Forex monetary row.')
        result[match[1]] = tuple(_amount(match[i].replace('$', '')) for i in range(2, columns + 2))
    return result


def _monthly_pairs(first):
    section = first.split('Monthly Profit/Loss Summary')[1].split('Open Position(s) at End of Month')[0]
    result = {}
    for line in section.splitlines():
        if re.match(r'^\s*[A-Z]{3}/[A-Z]{3}', line):
            match = re.fullmatch(rf'\s*([A-Z]{{3}}/[A-Z]{{3}})\s+({MONEY})(?:\s+{MONEY})?\s*', line)
            if match is None or match[1] in result:
                raise ValueError('Invalid monthly pair monetary summary.')
            result[match[1]] = _amount(match[2].replace('$', ''))
    return result


def _daily_reports(text):
    pages = [p.strip() for p in re.split(r'(?=^Customer Account Summary Report since )', text.replace('\f', '\n'), flags=re.M) if p.strip()]
    first = pages[0]
    previous, last = _coverage(first)
    days = []
    for page in pages[1:]:
        start, end = _coverage(page)
        if start != previous or end <= start or end > last:
            raise ValueError('Daily Forex statement coverage has a gap, overlap or wrong end date.')
        if page.count('Closed Position(s)') != 1 or page.count('Trade Activity Detail') != 1 or page.count('Profit/Loss Summary') != 1:
            raise ValueError('Daily financial report sections are missing or ambiguous.')
        closed = _pairs(page.split('Closed Position(s)')[1].split('Trade Activity Detail')[0], 1)
        table = _pairs(page.split('Profit/Loss Summary')[1].split('Open Position(s)')[0], 3)
        if {k: v[0] for k, v in closed.items() if v[0]} != {k: v[1] for k, v in table.items() if v[1]}:
            raise ValueError('Daily closed-position and pending-settlement USD controls differ.')
        # Zero P/L closes must remain represented even though their dollar amount is zero.
        days.append(dict(day=end, start=start, page=page, closed={k: v[0] for k, v in closed.items()}, settled={k: v[0] for k, v in table.items()}))
        previous = end
    if not days or previous != last:
        raise ValueError('Daily Forex reports do not cover the full monthly statement.')
    if any(days[0]['settled'].values()) or any(days[0]['closed'].values()):
        raise ValueError('Opening pending settlements require independent prior-period evidence.')
    return first, days


def resolve_daily_cash_statement(text, *, trade_history, transaction_report, period_end):
    if period_end not in SUPPORTED:
        raise ValueError('Daily cash source resolution is limited to March/November/December 2025.')
    paths = (Path(trade_history), Path(transaction_report))
    originals = tuple(p.read_bytes() for p in paths)
    proof = read_forex_opening_statement(text, trade_history=paths[0], transaction_report=paths[1], period_end=period_end)
    first, days = _daily_reports(text)
    raw = read_forex_transaction_report(paths[1])
    standard = (period_end.replace(day=1) - timedelta(days=1), period_end)
    # March's saved export spans March 1-April 1. Its zero MTD header belongs
    # to a different scope; all executions must still tie to monthly/history.
    permitted = (standard, (date(2025, 3, 1), date(2025, 4, 1))) if period_end.month == 3 else (standard,)
    if (raw.period_start, raw.period_end) not in permitted:
        raise ValueError('Unexpected original CSV coverage for financial source resolution.')
    for fields in csv.reader(io.StringIO(originals[1].decode('utf-8-sig'))):
        if len(fields) > 14 and fields[3].strip().lower() == 'financing':
            values = [_amount(fields[i].strip().replace(' USD', '').replace('$', '')) for i in (13, 14) if fields[i].strip()]
            if len(values) != 2 or values[0] != values[1]:
                raise ValueError('CSV financing amount and delivery amount disagree.')
    rows = read_forex_trades(ThinkorswimSourceReader().read(str(paths[0])).section('Forex Statements'))
    details = {}
    for line in text.splitlines():
        match = _TRADE.fullmatch(line)
        if match:
            if match[7] in details:
                raise ValueError('Duplicate monthly execution reference.')
            details[match[7]] = (match, datetime.fromisoformat(' '.join(line.split()[:2])))
    execution_days = {}
    for day in days:
        for line in day['page'].splitlines():
            match = _TRADE.fullmatch(line)
            if match:
                if match[7] in execution_days:
                    raise ValueError('Execution occurs in more than one daily report.')
                execution_days[match[7]] = day['day']
    if set(execution_days) != set(details):
        raise ValueError('Monthly executions are not all bound to daily report membership.')
    indexed = defaultdict(list)
    for event in (*raw.settlements, *raw.new_transactions):
        indexed[event.order_id].append(event)
    if set(indexed) - set(details):
        raise ValueError('CSV has execution references absent from monthly/history evidence.')
    resolver = ForexPositionEffectResolver(dict(proof.opening))
    positions = dict(proof.opening); close_groups = defaultdict(list); closes = []; news = []
    missing = []; discrepancies = []; clocks = []
    source = 'monthly:' + proof.source_sha256 + ';history:' + proof.history_sha256 + ';csv:' + proof.report_sha256
    for row in sorted(rows, key=lambda r: r.executed_at):
        match, observed = details[row.reference]
        effects = resolver.resolve(pair=row.pair, quantity=row.quantity)
        records = indexed.get(row.reference, [])
        if len(records) > 2 or len({type(r) for r in records}) != len(records):
            raise ValueError('Duplicate or unexplained split CSV execution.')
        if records and (sum((r.amount for r in records), ZERO) != row.quantity or any(r.instrument != row.pair or r.rate != row.price or abs(r.trade_at - observed) > timedelta(seconds=1) or r.side not in (('B', 'Buy') if row.quantity > 0 else ('S', 'Sell')) for r in records)):
            raise ValueError('CSV execution facts differ from exact monthly/history evidence.')
        clock_delta = observed - (row.executed_at + timedelta(hours=2))
        if abs(clock_delta) > timedelta(seconds=1) or (not records and clock_delta != timedelta(0)):
            raise ValueError('Monthly/history execution clocks do not corroborate.')
        if clock_delta or any(old.trade_at != observed for old in records):
            clocks.append(dict(reference=row.reference, monthly_at=observed.isoformat(), history_at=row.executed_at.isoformat(), csv_at=sorted({old.trade_at.isoformat() for old in records})))
        for effect in effects:
            closing = effect.position_effect.value == 'CLOSE'
            selected = [r for r in records if isinstance(r, SchwabForexSettlement) == closing]
            old = selected[0] if selected else None
            positions[row.pair] = positions.get(row.pair, ZERO) + effect.quantity
            if old is not None and (old.amount != effect.quantity or old.total_position != positions[row.pair]):
                raise ValueError('CSV split effect quantity or explicit Total Position contradicts replay.')
            if not closing:
                if old is None:
                    raise ValueError('Monthly opening fill lacks its original CSV financial record.')
                news.append(old)
                continue
            if row.broker_pnl_usd is None and (old is None or old.settlement_pl_usd != 0):
                raise ValueError('History close lacks USD P/L and explicit zero CSV close evidence.')
            if old is None:
                if len(effects) != 1 or raw.mtd_fee_usd != 0 or raw.commission_total_usd != 0:
                    raise ValueError('Missing close fees or split effect cannot be established.')
                missing.append(row.reference)
            item = SchwabForexSettlement(row.reference, old.trade_at if old else observed, old.settlement_at if old else None,
                row.pair, 'Buy' if effect.quantity > 0 else 'Sell', row.price, effect.quantity, row.broker_pnl_usd if row.broker_pnl_usd is not None else ZERO,
                positions[row.pair], old.fee_usd if old else ZERO, source)
            close_groups[(execution_days[row.reference], row.pair)].append((item, old, row.broker_pnl_usd))
    # Daily closed-position dollars are authoritative for their uniquely matched
    # close. Multiple closes retain history allocation only if their exact sum
    # equals the independent daily amount; no arbitrary penny allocation.
    expected_groups = {(d['day'], pair): amount for d in days for pair, amount in d['closed'].items()}
    zero_close_refs = []
    for key in close_groups.keys() - expected_groups.keys():
        group = close_groups[key]
        if all(old is not None and old.settlement_pl_usd == 0 and history in (None, ZERO) for _, old, history in group):
            expected_groups[key] = ZERO
            zero_close_refs.extend(item.order_id for item, _, _ in group)
    if set(close_groups) != set(expected_groups):
        raise ValueError('Daily closed positions and exact execution groups differ.')
    for key, group in close_groups.items():
        amount = expected_groups[key]
        if len(group) > 1 and sum((item.settlement_pl_usd for item, _, _ in group), ZERO) != amount:
            raise ValueError('Multiple closes lack an exact daily USD allocation.')
        for item, old, history_usd in group:
            resolved = replace(item, settlement_pl_usd=amount) if len(group) == 1 else item
            closes.append(resolved)
            if old is None or old.settlement_pl_usd != resolved.settlement_pl_usd or history_usd != resolved.settlement_pl_usd:
                discrepancies.append(dict(reference=item.order_id, csv_usd=None if old is None else str(old.settlement_pl_usd), history_usd=str(history_usd), monthly_daily_usd=str(resolved.settlement_pl_usd), settlement_date_reported=item.settlement_at is not None))
    financing = {}; inside = False
    for line in text.splitlines():
        value = line.strip()
        if value == 'Financing Report':inside = True;continue
        if value.startswith(('Conversion Report', 'Customer Account Summary Report')):inside = False
        if inside and re.match(r'^\d{4}-', value):
            parts = value.split()
            if len(parts) != 8 or not re.fullmatch(r'[A-Z]{3}/[A-Z]{3}', parts[2]) or not parts[5].isdigit() or parts[5] in financing:
                raise ValueError('Unsupported or duplicate monthly financing detail.')
            at = datetime.fromisoformat(' '.join(parts[:2]))
            if not period_end.replace(day=1)-timedelta(days=3) <= at.date() <= period_end:
                raise ValueError('Financing falls outside monthly scope.')
            financing[parts[5]] = SchwabForexFinancing(parts[5], at, 'USD$FX', _amount(parts[7].replace('$', '')))
    original_fin = {f.order_id: f for f in raw.financing}
    if len(original_fin) != len(raw.financing):raise ValueError('Duplicate original financing reference.')
    if any(k in original_fin and original_fin[k].financing_usd != f.financing_usd for k, f in financing.items()):
        raise ValueError('Individual financing amounts disagree between sources.')
    pair_totals = _monthly_pairs(first); settled = defaultdict(Decimal); trade = defaultdict(Decimal)
    for d in days:
        for pair, amount in d['settled'].items():settled[pair] += amount
        for pair, amount in d['closed'].items():trade[pair] += amount
    pending = days[-1]['closed']
    reported_cash = summary(first, 'Settled Profit/Loss'); reported_fin = summary(first, 'Financing')
    checks = dict(monthly_pair_sum=reported_cash-sum(pair_totals.values(), ZERO),
        daily_settled_sum=reported_cash-sum(settled.values(), ZERO),
        close_to_cash_bridge=reported_cash-(sum(trade.values(), ZERO)-sum(pending.values(), ZERO)),
        financing=reported_fin-sum((f.financing_usd for f in financing.values()), ZERO),
        cash_balance=summary(first, 'Ending Cash Balance')-(summary(first, 'Beginning Cash Balance')+summary(first, 'Deposits')-summary(first, 'Withdrawals')+summary(first, 'Adjustments')+reported_fin+reported_cash))
    pairs = set(pair_totals) | set(settled) | set(trade) | set(pending)
    for pair in pairs:
        checks['settled_pair:'+pair] = pair_totals.get(pair, ZERO)-settled.get(pair, ZERO)
        checks['trade_cash_pair:'+pair] = pair_totals.get(pair, ZERO)-(trade.get(pair, ZERO)-pending.get(pair, ZERO))
    if any(abs(v) > Decimal('.01') for v in checks.values()):
        raise ValueError('Independent monthly/daily cash controls exceed one-cent aggregate tolerance.')
    # Financing and trade allocations have exact independent detail controls.
    if checks['financing'] != 0:
        raise ValueError('Monthly financing differs from exact financing details.')
    trade_total = sum((s.settlement_pl_usd for s in closes), ZERO)
    if trade_total != sum(trade.values(), ZERO):raise ValueError('Allocated close USD differs from daily details.')
    resolved = replace(raw, settlements=tuple(closes), new_transactions=tuple(news), financing=tuple(financing.values()),
        mtd_settled_pl_usd=reported_cash, pl_total_usd=trade_total, financing_totals_usd=(('USD$FX', reported_fin),),
        source_policy=POLICY, pending_settlement_pl_usd=sum(pending.values(), ZERO))
    if not resolved.commission_control_reconciled:raise ValueError('Original commission control is missing or failed.')
    if not resolved.settlement_control_reconciled or not resolved.pl_total_control_reconciled:raise ValueError('Resolved financial controls failed.')
    audit = dict(format='campaigniq.forex_source_resolution', version=2, policy=POLICY, period_end=period_end.isoformat(),
        source_sha256=dict(monthly_text=proof.source_sha256, history=proof.history_sha256, original_csv=proof.report_sha256),
        accounting_basis='Trade-date close P/L; independent monthly settled cash and pending-close bridge.',
        resolved_pl_usd=str(trade_total), reported_monthly_pl_usd=str(reported_cash), financing_usd=str(reported_fin),
        pending_settlement_pl_usd=str(resolved.pending_settlement_pl_usd), pending_by_pair_usd={k:str(v) for k,v in pending.items()},
        missing_csv_closes=missing, settlement_changes=discrepancies, timestamp_differences=clocks, zero_closes_omitted_from_daily_dollar_table=zero_close_refs,
        timestamp_policy="Exact reference, date, pair, quantity, rate and effect; known two-hour clock offset plus at most one second of source granularity, retained verbatim. Missing closes require exact clock alignment.",
        original_csv_controls=dict(period_start=raw.period_start.isoformat(), period_end=raw.period_end.isoformat(), mtd_pl_usd=str(raw.mtd_settled_pl_usd), settlement_row_total_usd=str(raw.settlement_pl_usd), pl_total_usd=None if raw.pl_total_usd is None else str(raw.pl_total_usd)),
        monthly_financing_ids=sorted(financing), monthly_financing_records=[dict(reference=f.order_id, observed_at=f.occurred_at.isoformat(), amount_usd=str(f.financing_usd)) for f in financing.values()],
        csv_financing_outside_monthly_scope=[dict(reference=f.order_id,instrument=f.instrument,amount_usd=str(f.financing_usd)) for f in raw.financing if f.order_id not in financing],
        monthly_financing_absent_from_csv=sorted(financing.keys()-original_fin.keys()),
        aggregate_differences_usd={k:str(v) for k,v in checks.items()},
        settlement_dates_not_invented=True, original_sources_unchanged=True)
    if originals != tuple(p.read_bytes() for p in paths):raise ValueError('Financial source changed during resolution.')
    return resolved, audit
