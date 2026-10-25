"""Read-only development preview of supplemental Forex import candidates.

This proposes a source policy for review. It does not repair the CSV, activate
that policy, map canonical campaigns, or authorize any import/publication.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import json
import re
import subprocess
from tempfile import TemporaryDirectory

from campaigniq.domain.position_effect import PositionEffect
from campaigniq.forex_monetary_audit import audit_forex_money, _monthly_money
from campaigniq.forex_supplemental_audit import (
    _amount, _HEADER, _POSITION, _TRADE, read_monthly_forex_evidence,
)
from campaigniq.historical_backfill_ancestry import _development
from campaigniq.importers.schwab.forex_transaction_reader import read_forex_transaction_report
from campaigniq.importers.thinkorswim.forex_position_effect_resolver import ForexPositionEffectResolver
from campaigniq.importers.thinkorswim.forex_trade_reader import read_forex_trades
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader


def _first_daily_positions(text, monthly, rows):
    pages = [p for p in text.split('\f') if p.strip()]
    if len(pages) < 2:
        raise ValueError('First daily opening-position corroboration is required.')
    first = pages[1]
    header = _HEADER.fullmatch(first.splitlines()[0].strip())
    if header is None:
        raise ValueError('Unsupported first daily statement coverage.')
    start = datetime.strptime(header['start'], '%B %d, %Y').date()
    end = datetime.strptime(header['end'], '%B %d, %Y').date()
    expected = monthly.period_start + timedelta(days=1)
    while expected.weekday() >= 5:
        expected += timedelta(days=1)
    if start != monthly.period_start or end != expected:
        raise ValueError('First daily statement does not corroborate the opening boundary.')
    # Its closing snapshot can corroborate opening quantities only when no
    # selected-month executions have occurred before that daily close.
    if any(row.executed_at.date() <= end for row in rows):
        raise ValueError('First daily positions follow executions; an independent opening snapshot is required.')
    if first.count('Open Position(s)') != 1 or first.count('Closed Position(s)') != 1:
        raise ValueError('Daily opening positions are missing or ambiguous.')
    section = first.split('Open Position(s)')[1].split('Closed Position(s)')[0]
    positions = {}
    for line in section.splitlines():
        if 'TOTAL POSITION:' not in line:
            continue
        match = _POSITION.match(line.strip())
        if match is None or match[1] in positions:
            raise ValueError('Unrecognized or duplicate first daily position row.')
        pair, side, number = match.groups()
        quantity = _amount(number)
        if quantity == 0 or (side == 'Buy') != (quantity > 0):
            raise ValueError('Daily position side and quantity disagree.')
        positions[pair] = quantity
    if not positions:
        raise ValueError('Explicit first daily opening-position totals are required.')
    return positions


def _opening_candidates(path, monthly):
    report = read_forex_transaction_report(path)
    end = monthly.period_start
    expected_start = end.replace(day=1) - timedelta(days=1)
    if report.period_end != end or report.period_start != expected_start:
        raise ValueError('Opening Forex report must cover the preceding standard month.')
    all_rows = (*report.new_transactions, *report.settlements)
    identifiers = [r.order_id for r in all_rows]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError('Duplicate opening-report execution reference.')
    pairs = {r.instrument for r in all_rows}
    positions, anchors = {}, []
    for pair in sorted(pairs):
        candidates = [r for r in all_rows if r.instrument == pair]
        when = max(r.trade_at for r in candidates)
        last = [r for r in candidates if r.trade_at == when]
        if len(last) != 1:
            raise ValueError('Ambiguous final opening-report execution order.')
        row = last[0]
        if (not row.total_position.is_finite() or not row.amount.is_finite()
                or not row.rate.is_finite() or row.rate <= 0):
            raise ValueError('Invalid opening-report position, quantity or rate.')
        if row.total_position:
            # A final explicit new-from-flat position supplies a strong anchor;
            # partial carries/unknown earlier inventory require further evidence.
            if (row not in report.new_transactions or row.trade_at.date() != end
                    or row.total_position != row.amount):
                raise ValueError('Opening position requires an explicit boundary new-from-flat anchor.')
            positions[pair] = row.total_position
            anchors.append({'pair': pair, 'quantity': str(row.total_position),
                'reference': row.order_id, 'rate': str(row.rate),
                'source': 'preceding_transaction_report_final_position'})
    return positions, anchors


def build_forex_import_preview(*, monthly_text, transaction_report, trade_history,
        opening_transaction_report, period_end, development_only=False):
    """Construct source-bound candidates, independently checking both boundaries.

    Candidate USD P/L comes from the execution history corroborated by monthly
    pair totals; financing comes from explicit monthly rows. The original CSV
    controls, omissions and disagreements remain attached as unresolved evidence.
    """
    _development(development_only)
    paths = tuple(Path(p).resolve() for p in (transaction_report, trade_history, opening_transaction_report))
    if len(set(paths)) != 3:
        raise ValueError('Three distinct CSV evidence sources are required.')
    before = tuple(p.read_bytes() for p in paths)
    monthly = read_monthly_forex_evidence(monthly_text, period_end=period_end)
    money = audit_forex_money(monthly_text=monthly_text, transaction_report=paths[0],
        trade_history=paths[1], period_end=period_end, development_only=True)
    if not money['monthly_money_controls_verified']:
        raise ValueError('Corroborated monthly money controls did not pass.')
    # Compare subaccount identity; the securities account's different ID is not
    # assumed to equal the Forex account. Matching executions corroborate history.
    accounts = [re.findall(r'Account \(USD\):\s*(\d+)', data.decode('utf-8-sig'))
                for data in (before[0], before[2])]
    if any(len(a) != 1 for a in accounts) or accounts[0] != accounts[1]:
        raise ValueError('Opening/current Forex reports must identify the same account.')
    rows = [row for row in read_forex_trades(ThinkorswimSourceReader().read(str(paths[1])).section('Forex Statements'))
            if period_end.replace(day=1) <= row.executed_at.date() <= period_end]
    if len({r.reference for r in rows}) != len(rows):
        raise ValueError('Duplicate history execution reference.')
    opening, anchors = _opening_candidates(paths[2], monthly)
    corroboration = _first_daily_positions(monthly_text, monthly, rows)
    if opening != corroboration:
        raise ValueError('Opening report quantities disagree with first daily statement.')
    # Read the monthly explicit effects as well as exact execution IDs, dates,
    # pairs, quantities and rates. Calendar dates are compared; no time-zone
    # equivalence is asserted.
    details = {}
    inside = False
    for line in monthly_text.splitlines():
        s = line.strip()
        if s == 'Trade Activity Detail':
            inside = True; continue
        if s.startswith(('Financing Report', 'Conversion Report', 'Customer Account Summary Report')):
            inside = False
        match = _TRADE.fullmatch(line) if inside else None
        if match is not None:
            if match[7] in details:
                raise ValueError('Duplicate monthly execution reference.')
            details[match[7]] = match
    if set(details) != {r.reference for r in rows}:
        raise ValueError('Monthly/history executions are incomplete or mismatched.')
    positions = dict(opening)
    resolver = ForexPositionEffectResolver(initial_positions=opening)
    candidates = []
    tied = {}
    for row in rows:
        tied.setdefault((row.executed_at, row.pair), []).append(row.reference)
    for references in tied.values():
        if len(references) > 1 and [identifier for identifier in details if identifier in references] != references:
            raise ValueError('Equal-time execution order differs between history and monthly detail.')
    for row in sorted(rows, key=lambda r: r.executed_at):
        detail = details[row.reference]
        if (not row.quantity.is_finite() or row.quantity == 0 or not row.price.is_finite()
                or row.price <= 0 or (row.action == 'BOT') != (row.quantity > 0)):
            raise ValueError('Invalid Forex execution direction, quantity or rate.')
        if (detail[1] != row.executed_at.date().isoformat() or detail[2] != row.pair
                or _amount(detail[5]) != row.quantity or _amount(detail[6]) != row.price):
            raise ValueError('Monthly/history execution differs; no quantity/rate tolerance applies.')
        effects = resolver.resolve(pair=row.pair, quantity=row.quantity)
        if len(effects) != 1 or effects[0].position_effect.value != detail[4]:
            raise ValueError('Opening inventory produces a different execution effect from monthly evidence.')
        effect = effects[0].position_effect
        if effect is PositionEffect.CLOSE:
            if row.broker_pnl_usd is None or not row.broker_pnl_usd.is_finite():
                raise ValueError('Closing execution requires explicit finite history USD P/L.')
            pl = str(row.broker_pnl_usd)
        else:
            if row.broker_pnl_usd not in (None, Decimal(0)):
                raise ValueError('Opening execution has unexplained nonzero USD P/L.')
            pl = None
        positions[row.pair] = positions.get(row.pair, Decimal(0)) + row.quantity
        candidates.append({'reference': row.reference, 'executed_at': row.executed_at.isoformat(),
            'pair': row.pair, 'quantity': str(row.quantity), 'rate': str(row.price),
            'position_effect': effect.value, 'settled_pl_usd_candidate': pl,
            'ordering_basis': ('history_and_monthly_detail_order' if len(tied[(row.executed_at, row.pair)]) > 1
                               else 'history_execution_timestamp'),
            'execution_source': 'history_and_monthly_statement_exact_match',
            'pl_source': 'history_corroborated_by_monthly_pair_totals' if pl is not None else None,
            'canonical_campaign_id': None})
    positions = {p: q for p, q in positions.items() if q}
    if positions != dict(monthly.positions):
        raise ValueError('Replayed closing Forex quantities differ from monthly statement.')
    _, finance = _monthly_money(monthly_text)
    financing_candidates = [dict(reference=identifier, date=value['date'], pair=value['pair'],
        amount_usd=str(value['amount']), source='monthly_financing_detail')
        for identifier, value in sorted(finance.items())]
    if before != tuple(p.read_bytes() for p in paths):
        raise ValueError('Source evidence changed while building preview.')
    return {'format': 'campaigniq.forex_import_preview', 'version': 1,
        'period_end': period_end.isoformat(), 'preview_only': True,
        'candidate_inputs_verified': True, 'opening_positions_corroborated': True,
        'opening_position_candidates': anchors,
        'execution_candidates': candidates, 'financing_candidates': financing_candidates,
        'closing_quantities': {p: str(q) for p, q in sorted(positions.items())},
        'closing_quantities_reconciled': True,
        'candidate_settled_pl_usd': str(sum((Decimal(c['settled_pl_usd_candidate'])
            for c in candidates if c['settled_pl_usd_candidate'] is not None), Decimal(0))),
        'candidate_financing_usd': str(sum((v['amount'] for v in finance.values()), Decimal(0))),
        'monthly_reported_settled_pl_usd': str(monthly.settled_pl),
        'proposed_source_policy': 'Use corroborated history executions/USD P&L and monthly financing details; retain original CSV disagreements for explicit review.',
        'original_csv_monetary_audit': money,
        'source_sha256': {'monthly_extracted_text': sha256(monthly_text.encode()).hexdigest(),
            'transaction_report': sha256(before[0]).hexdigest(), 'trade_history': sha256(before[1]).hexdigest(),
            'opening_transaction_report': sha256(before[2]).hexdigest()},
        'canonical_campaigns_verified': False, 'full_continuity_verified': False,
        'import_authorized': False, 'publication_authorized': False,
        'limitation': 'Candidate evidence only; source policy is not activated. No import, database write, CSV repair, automatic exception, canonical campaign mapping or publication.'}


def main():
    import argparse
    from campaigniq.pdf_text import extract_pdf_text
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--development-only', required=True, action='store_true')
    p.add_argument('--period-end', type=date.fromisoformat, required=True)
    p.add_argument('--monthly-statement', type=Path, required=True)
    p.add_argument('--transaction-report', type=Path, required=True)
    p.add_argument('--trade-history', type=Path, required=True)
    p.add_argument('--opening-transaction-report', type=Path, required=True)
    args = p.parse_args()
    try:
        _development(args.development_only)
        content = args.monthly_statement.read_bytes()
        with TemporaryDirectory(prefix='campaigniq-forex-preview-') as directory:
            copied = Path(directory) / 'monthly.pdf'; copied.write_bytes(content)
            text = extract_pdf_text(copied, Path(directory) / 'monthly.txt').read_text()
            result = build_forex_import_preview(monthly_text=text, transaction_report=args.transaction_report,
                trade_history=args.trade_history, opening_transaction_report=args.opening_transaction_report,
                period_end=args.period_end, development_only=True)
        if content != args.monthly_statement.read_bytes():
            raise ValueError('Monthly PDF changed during preview.')
        result['source_sha256']['monthly_pdf'] = sha256(content).hexdigest()
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'error': type(error).__name__, 'message': str(error),
                          'import_authorized': False, 'publication_authorized': False}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
