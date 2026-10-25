import calendar
from copy import deepcopy
from datetime import date
import json

import pytest

from campaigniq.historical_backfill_market_continuity import MONTHS, audit_markets
from campaigniq.persistence.crypto_month import build_crypto_month


def artifacts():
    result = {}
    previous = None
    for month in MONTHS:
        y, m = map(int, month.split('-'))
        start, end = date(y, m, 1).isoformat(), date(y, m, calendar.monthrange(y, m)[1]).isoformat()
        common = dict(version=1, period_start=start, period_end=end)
        crypto = dict(common, format='campaigniq.crypto_month', account='Crypto #', fills=[],
            holdings_snapshot={}, warnings=[], opening_cash_usd=None, last_reported_cash_usd=None)
        crypto = build_crypto_month(crypto, previous)
        objects = {'lot-book': dict(common, format='campaigniq.lot_book', lots=[]),
            'position-journal': dict(common, format='campaigniq.position_journal', entries=[]),
            'forex-settlement-attributions': dict(common, format='campaigniq.forex_settlement_attributions', attributions=[]),
            'crypto': crypto}
        result.update({month + '-' + k + '.json': json.dumps(v) for k, v in objects.items()})
        previous = crypto
    return result


def change(data, key, mutate):
    obj = json.loads(data[key])
    mutate(obj)
    data[key] = json.dumps(obj)


def test_empty_placeholder_is_continuous_but_not_statement_confirmation():
    report = audit_markets(artifacts())
    assert report['retained_market_ledger_continuity_verified']
    assert report['monthly_boundary_count'] == 20
    assert not report['independent_crypto_month_end_evidence_complete']
    assert not report['full_portfolio_continuity_verified']
    assert not report['publication_authorized']
    assert len(report['source_artifact_sha256']) == 84


@pytest.mark.parametrize('key', ['2025-12-crypto.json', '2026-01-position-journal.json'])
def test_missing_year_boundary_fails(key):
    data = artifacts()
    del data[key]
    with pytest.raises(KeyError):
        audit_markets(data)


def test_forex_boundary_break_fails_even_when_month_replays():
    data = artifacts()
    change(data, '2025-12-lot-book.json', lambda o: o['lots'].append(dict(instrument=dict(type='instrument',symbol='EUR/USD'),quantity='50000')))
    change(data, '2025-12-position-journal.json', lambda o: o['entries'].append(dict(instrument=dict(type='instrument',symbol='EUR/USD'),action='Open / add',quantity_change='50000')))
    with pytest.raises(ValueError, match='2026-01'):
        audit_markets(data)


def test_crypto_basis_tamper_fails():
    data = artifacts()
    change(data, '2026-09-crypto.json', lambda o: o.update(realized_pnl_usd='1'))
    with pytest.raises(ValueError, match='Crypto continuous'):
        audit_markets(data)


def test_crypto_cash_boundary_break_fails():
    data = artifacts()
    change(data, '2025-12-crypto.json', lambda o: o.update(last_reported_cash_usd='100'))
    change(data, '2026-01-crypto.json', lambda o: o.update(opening_cash_usd='99'))
    with pytest.raises(ValueError, match='opening cash'):
        audit_markets(data)


@pytest.mark.parametrize('kind', ['missing_campaign', 'duplicate'])
def test_settlement_integrity(kind):
    data = artifacts()
    row = dict(campaign_id='fx-campaign', settlement=dict(order_id='123', trade_at='2025-12-01T10:00:00',
        settlement_at='2025-12-03T10:00:00', instrument='EUR/USD', side='SELL', rate='1.1',
        amount='-50000', settlement_pl_usd='12.34', total_position='0'))
    if kind == 'missing_campaign':
        row['campaign_id'] = ''
    change(data, '2025-12-forex-settlement-attributions.json', lambda o: o['attributions'].append(row))
    if kind == 'duplicate':
        change(data, '2026-01-forex-settlement-attributions.json', lambda o: o['attributions'].append(deepcopy(row)))
    with pytest.raises(ValueError):
        audit_markets(data)


def test_nonfinite_quantity_fails():
    data = artifacts()
    change(data, '2025-12-lot-book.json', lambda o: o['lots'].append(dict(instrument=dict(type='instrument',symbol='EUR/USD'),quantity='NaN')))
    with pytest.raises(ValueError, match='Non-finite'):
        audit_markets(data)


def test_source_hash_and_no_input_mutation():
    data = artifacts()
    before = deepcopy(data)
    first = audit_markets(data)
    change(data, '2025-01-crypto.json', lambda o: o['warnings'].append('Retained source warning'))
    second = audit_markets(data)
    assert data != before
    assert first['source_artifact_sha256'] != second['source_artifact_sha256']
    assert second['months'][0]['retained_crypto_warnings'][-1] == 'Retained source warning'
    baseline = deepcopy(data)
    audit_markets(data)
    assert data == baseline
