from copy import deepcopy
from decimal import Decimal as D
import json
import subprocess
import sys

import pytest
from campaigniq.crypto_statement_policy_preview import build_preview,preview_files,SOURCE_ROLES
from tests.test_crypto_source_review import evidence


def candidate(evidence,**kwargs):
    return build_preview(*evidence,source_sha256=kwargs.get('hashes',{r:'a'*64 for r in SOURCE_ROLES}))


def test_statement_cash_costs_and_carryforward_are_proposals_with_complete_discrepancies(evidence):
    before=deepcopy(evidence);r=candidate(evidence);body=r.to_dict();t=body['totals']
    assert D(t['proposed_opening_cash_usd'])==D('3.75') and D(t['proposed_ending_cash_usd'])==0
    assert D(t['purchases_including_fees_usd'])==D('10.11') and D(t['fees_already_in_trade_cash_usd'])==D('.10')
    assert D(t['known_ending_lot_cost_usd'])==D('10.11') # no double fee
    assert t['full_ending_cost_basis_usd'] is None and not body['ending_cost_basis_complete']
    assert body['ending_lots']['BTC/USD'][0]['cost_usd'] is None
    assert D(t['proposed_realized_sales_pnl_usd'])==0
    assert D(t['proposed_value_change_after_external_funding_usd'])==D('-.11')
    assert body['review']['review_required'] and body['review']['trade_cash_differences']
    assert body['source_choice_rows'][0]['original_export_net_cash_usd']=='-10.10'
    for key in ('policy_approved','account_association_confirmed','original_discrepancies_resolved','financial_state_written','publication_authorized'):
        assert body[key] is False
    assert evidence==before and candidate(evidence)==r
    body['totals']['purchases_including_fees_usd']='0'
    assert D(r.to_dict()['totals']['purchases_including_fees_usd'])==D('10.11')


@pytest.mark.parametrize('hashes',[{},None,{'trade_history':'a'*64},{r:'bad' for r in SOURCE_ROLES}])
def test_missing_source_fingerprints_cannot_produce_candidate(evidence,hashes):
    with pytest.raises(ValueError):candidate(evidence,hashes=hashes)


@pytest.mark.parametrize('change',['quantity','fee','price','funding','coverage','ending_cash','end_units','unknown_ledger'])
def test_evidence_failures_cannot_be_approved_by_selecting_statement_sources(evidence,change):
    e,p,c=evidence
    if change=='quantity':c['transactions'][0]['quantity']='0.002'
    elif change=='fee':c['transactions'][0]['fee_usd']='0.11'
    elif change=='price':c['transactions'][0]['price_usd']='9999'
    elif change=='funding':c['transactions'][1]['amount_usd']='6.35'
    elif change=='coverage':e['export_end']='2026-10-30'
    elif change=='ending_cash':e['last_reported_cash_usd']='1'
    elif change=='end_units':c['holdings']['BTC/USD']['quantity']='0.1'
    else:e['cash_ledger'][0]['type']='UNKNOWN'
    with pytest.raises(ValueError):candidate(evidence)


def test_sale_consuming_carried_inventory_keeps_realized_cost_unknown(evidence):
    e,p,c=evidence
    e['fills'][0]['side']='SELL';e['fills'][0]['principal_usd']='10.00'
    e['cash_ledger']=[];e['last_reported_cash_usd']='13.65'
    c['transactions']=[{**c['transactions'][0],'action':'SELL','amount_usd':'9.90'}]
    c['ending_cash_usd']='13.65';c['holdings']['BTC/USD'].update(quantity='0.009',market_value_usd='90.00')
    body=candidate(evidence).to_dict()
    assert body['totals']['proposed_realized_sales_pnl_usd'] is None
    assert not body['realized_sales_basis_complete']
    assert body['fifo_allocations'][0]['cost_usd'] is None
    assert D(body['ending_quantities']['BTC/USD'])==D('.009')


def test_same_month_buy_then_sale_uses_statement_cost_and_net_proceeds_once(evidence):
    e,p,c=evidence
    e['fills'].append({**e['fills'][0],'fill_id':'history-row-2','executed_at':'2026-10-09T10:00:00','side':'SELL','principal_usd':'11.00','price_usd':'11000.12'})
    c['transactions'].append({**c['transactions'][0],'date':'2026-10-09','action':'SELL','price_usd':'11000.12','amount_usd':'10.90'})
    p['holdings']={};c['holdings']={};e['last_reported_cash_usd']='10.90';c['ending_cash_usd']='10.90'
    body=candidate(evidence).to_dict();t=body['totals']
    assert D(t['proposed_realized_sales_pnl_usd'])==D('.79')
    assert D(t['fees_already_in_trade_cash_usd'])==D('.20')
    assert not body['ending_lots']['BTC/USD']


def test_hashes_and_review_digest_change_with_each_exact_source(evidence):
    base=candidate(evidence)
    for role in SOURCE_ROLES:
        hashes={r:'a'*64 for r in SOURCE_ROLES};hashes[role]='b'*64
        changed=candidate(evidence,hashes=hashes)
        assert changed.sha256!=base.sha256
        assert changed.to_dict()['review_sha256']!=base.to_dict()['review_sha256']


def test_cli_missing_files_leaves_no_records_or_authority(tmp_path):
    missing=str(tmp_path/'absent')
    result=subprocess.run([sys.executable,'-m','campaigniq.crypto_statement_policy_preview','--development-only',
        '--trade-history',missing,'--previous-statement',missing,'--current-statement',missing,'--year','2026','--month','10'],capture_output=True,text=True)
    assert result.returncode==1 and json.loads(result.stdout)['publication_authorized'] is False
    assert not list(tmp_path.iterdir())
