"""Offline Forex and crypto continuity checks over source-bound artifacts.

Verifies retained ledger continuity; does not certify missing statement evidence,
Forex financing completeness, brokerage basis, or publication readiness.
"""
import argparse
import calendar
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import uuid

from campaigniq.historical_backfill_publication_package import canonical, digest
from campaigniq.persistence.crypto_month import build_crypto_month
from campaigniq.persistence.forex_settlement_attribution_store import deserialize_forex_settlement_attributions


MONTHS = tuple([f'2025-{m:02}' for m in range(1, 13)] + [f'2026-{m:02}' for m in range(1, 10)])
CRYPTO_FIELDS = ('opening_quantities', 'ending_quantities', 'ending_lots', 'realized_sales', 'realized_pnl_usd')


def number(value):
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError('Non-finite monetary or quantity value.')
    return result


def forex(instrument):
    return instrument.get('type') == 'instrument' and '/' in instrument.get('symbol', '')


def differences(left, right):
    return {key: [str(left.get(key, Decimal(0))), str(right.get(key, Decimal(0)))]
            for key in sorted(left.keys() | right.keys()) if left.get(key, Decimal(0)) != right.get(key, Decimal(0))}


def audit_markets(artifacts):
    """Read only. Missing periods and inconsistent ledgers fail explicitly."""
    rows = []
    sources = {}
    previous_fx = previous_crypto = None
    identities = set()
    settlement_count = 0
    settlement_total = Decimal(0)
    for month in MONTHS:
        year, m = map(int, month.split('-'))
        start = date(year, m, 1)
        end = date(year, m, calendar.monthrange(year, m)[1])

        def read(suffix, expected_format):
            key = month + '-' + suffix + '.json'
            text = artifacts[key]
            sources[key] = digest(text.encode())
            obj = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON.')))
            if obj.get('format') != expected_format or obj.get('period_end') != end.isoformat():
                raise ValueError('Artifact format or period differs: ' + key)
            if suffix != 'lot-book' and obj.get('period_start') != start.isoformat():
                raise ValueError('Artifact start differs: ' + key)
            return obj

        book = read('lot-book', 'campaigniq.lot_book')
        journal = read('position-journal', 'campaigniq.position_journal')
        closing, opening, changes = {}, {}, {}
        for lot in book['lots']:
            if forex(lot['instrument']):
                pair = lot['instrument']['symbol']
                closing[pair] = closing.get(pair, Decimal(0)) + number(lot['quantity'])
        for entry in journal['entries']:
            if forex(entry['instrument']):
                pair = entry['instrument']['symbol']
                target = opening if entry['action'] == 'Carried position' else changes
                target[pair] = target.get(pair, Decimal(0)) + number(entry['quantity_change'])
        boundary = differences(previous_fx, opening) if previous_fx is not None else {}
        replay = differences({pair: opening.get(pair, Decimal(0)) + changes.get(pair, Decimal(0))
                              for pair in opening.keys() | changes.keys()}, closing)
        if boundary or replay:
            raise ValueError('Forex position continuity differs: ' + month)
        read('forex-settlement-attributions', 'campaigniq.forex_settlement_attributions')
        settlements = deserialize_forex_settlement_attributions(artifacts[month + '-forex-settlement-attributions.json'])
        monthly_total = Decimal(0)
        for attribution in settlements.attributions:
            s = attribution.settlement
            identity = (s.order_id, s.instrument, s.trade_at.isoformat(), str(s.amount))
            if identity in identities or not attribution.campaign_id:
                raise ValueError('Duplicate Forex settlement or missing campaign: ' + month)
            identities.add(identity)
            monthly_total += number(s.settlement_pl_usd)
            for value in (s.rate, s.amount, s.total_position):
                number(value)
        settlement_count += len(settlements.attributions)
        settlement_total += monthly_total
        crypto = read('crypto', 'campaigniq.crypto_month')
        rebuilt = build_crypto_month(crypto, previous_crypto)
        if any(crypto.get(field) != rebuilt.get(field) for field in CRYPTO_FIELDS):
            raise ValueError('Crypto continuous FIFO replay differs: ' + month)
        # Cash comparisons are possible only when both sources report a balance.
        cash_known = previous_crypto is not None and previous_crypto.get('last_reported_cash_usd') is not None and crypto.get('opening_cash_usd') is not None
        if cash_known and number(previous_crypto['last_reported_cash_usd']) != number(crypto['opening_cash_usd']):
            raise ValueError('Crypto opening cash differs: ' + month)
        rows.append(dict(month=month, forex_boundary_comparison_available=previous_fx is not None,
            forex_boundary_quantity_match=True, forex_position_replay_match=True,
            forex_settlement_count=len(settlements.attributions), forex_settlement_pl_usd=str(monthly_total),
            forex_closing_quantities={k: str(v) for k, v in sorted(closing.items()) if v},
            crypto_continuous_fifo_match=True, crypto_fill_count=len(crypto['fills']),
            crypto_ending_quantities=crypto['ending_quantities'],
            crypto_cash_boundary_comparison_available=cash_known,
            crypto_month_end_evidence_confirmed=crypto.get('month_end_reconciled') is True,
            retained_crypto_warnings=crypto.get('warnings', [])))
        previous_fx, previous_crypto = closing, rebuilt
    return dict(format='campaigniq.market_continuity_review', version=1, months=rows,
        source_artifact_sha256=sources, monthly_boundary_count=len(MONTHS)-1,
        retained_market_ledger_continuity_verified=True,
        forex_settlement_count=settlement_count, forex_settlement_pl_usd=str(settlement_total),
        forex_settlement_duplicate_check_verified=True,
        independent_crypto_month_end_evidence_complete=all(r['crypto_month_end_evidence_confirmed'] for r in rows),
        limitations=['Retained ledgers are not independent confirmation of missing month-end statements.',
            'Forex financing completeness and brokerage basis provenance are outside this audit.',
            'Campaign assignment presence is checked; settlement-to-execution matching is not re-derived.'],
        production_connection_made=False, full_portfolio_continuity_verified=False, publication_authorized=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--development-only', action='store_true', required=True)
    args = parser.parse_args()
    if os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):
        parser.error('Run the offline audit locally.')
    from campaigniq.historical_backfill_restored_publisher import load_inputs
    package, bundle, candidates = load_inputs(args.package.expanduser(), args.capture.expanduser())
    report = audit_markets(bundle['artifacts'] | candidates)
    report['capture_sha256'] = package['capture_sha256']
    report['publication_package_sha256'] = digest((args.package.expanduser() / 'publication-package.json').read_bytes())
    output = Path.home() / 'Downloads' / ('CampaignIQ-market-continuity-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    path = output / 'market-continuity-review.json'
    with path.open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(canonical(report) + b'\n')
    print('Forex monthly position boundaries: 20/20 PASS')
    print('Forex settlements checked:', report['forex_settlement_count'])
    print('Crypto continuous FIFO replay: 21/21 PASS')
    print('Independent crypto month-end evidence complete:', report['independent_crypto_month_end_evidence_complete'])
    print('Report:', path)
    print('Offline audit only. No database connection or publication.')


if __name__ == '__main__':
    main()
