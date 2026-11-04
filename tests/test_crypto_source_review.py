from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest
from campaigniq.crypto_source_review import compare_sources,review_files


@pytest.fixture
def evidence():
    prior=dict(period_start='2026-09-01',period_end='2026-09-30',account_suffix='0841',
        beginning_cash_usd='0.00',ending_cash_usd='3.75',holdings={'BTC/USD':dict(quantity='0.010',price_usd='10000.00',market_value_usd='100.00')},
        transactions=[dict(date='2026-09-28',action='DEPOSIT',pair='CASH',quantity='0',price_usd='0',fee_usd='0',amount_usd='3.75')])
    current=dict(period_start='2026-10-01',period_end='2026-10-31',account_suffix='0841',
        beginning_cash_usd='0.00',ending_cash_usd='0.00',holdings={'BTC/USD':dict(quantity='0.011',price_usd='10000.00',market_value_usd='110.00')},
        transactions=[dict(date='2026-10-08',action='BUY',pair='BTC/USD',quantity='0.001',price_usd='10000.12',fee_usd='0.10',amount_usd='-10.11'),
            dict(date='2026-10-08',action='DEPOSIT',pair='CASH',quantity='0',price_usd='0',fee_usd='0',amount_usd='6.36')])
    export=dict(period_start='2026-10-01',period_end='2026-10-31',export_start='2026-09-30',export_end='2026-10-31',account='Synthetic0841',
        fills=[dict(fill_id='history-row-1',executed_at='2026-10-08T10:00:00',side='BUY',pair='BTC/USD',quantity='0.001',price_usd='10000.12345',principal_usd='10.00',fee_usd='0.10')],
        cash_ledger=[dict(ledger_id='crypto-row-1',type='BAL',executed_at='2026-10-01T00:00:00',amount_usd='0',fee_cash_change_usd='0'),
            dict(ledger_id='crypto-row-2',type='FND',executed_at='2026-10-08T09:59:00',amount_usd='6.36',fee_cash_change_usd='0'),
            dict(ledger_id='crypto-row-3',type='TRD',executed_at='2026-10-08T10:00:00',amount_usd='-10.00',fee_cash_change_usd='-0.10')],
        opening_cash_usd='3.74',last_reported_cash_usd='0.00',cash_control_reconciled=False,cash_control_deltas_usd=['-0.01','0.01'],fill_control_reconciled=True)
    return export,prior,current


def test_precise_carryforward_and_individual_cash_differences_are_reviewed_without_mutation(evidence):
    before=deepcopy(evidence);result=compare_sources(*evidence)
    assert result['all_matched_evidence_checks_pass']
    assert result['review_required'] and not result['all_controls_reconciled']
    assert result['publication_authorized'] is result['source_adjustments_applied'] is False
    assert result['opening_cost_basis_established'] is False
    assert result['cash']['statement_rollforward_using_preceding_closing_delta_usd']=='0.00'
    assert result['cash']['export_overall_rollforward_delta_usd']=='0.00'
    assert result['cash']['current_printed_opening_usd']=='0.00'
    row=result['trade_cash_differences'][0]
    assert Decimal(row['statement_minus_export_cash_usd'])==Decimal('-.01')
    assert row['price_relationship']=='consistent_with_printed_truncation'
    assert result['export_running_balance_deltas_usd']==['-0.01','0.01']
    assert evidence==before


@pytest.mark.parametrize('source,field,value',[(0,'period_start','2026-09-01'),(1,'period_end','2026-08-31'),
    (1,'account_suffix','9999'),(2,'account_suffix','9999'),(0,'account','no-suffix')])
def test_scope_and_suffix_conflicts_block_review(evidence,source,field,value):
    evidence[source][field]=value
    with pytest.raises(ValueError):compare_sources(*evidence)


@pytest.mark.parametrize('field,value',[('quantity','0.002'),('fee_usd','0.11'),('price_usd','10000.13')])
def test_no_tolerance_for_changed_quantity_fee_or_incompatible_price(evidence,field,value):
    evidence[2]['transactions'][0][field]=value
    result=compare_sources(*evidence)
    assert not result['checks']['all_partial_fills_paired'] and not result['all_controls_reconciled']


def test_duplicate_partial_fills_are_not_deduplicated_or_matched_by_reference(evidence):
    e,p,c=evidence
    second=deepcopy(e['fills'][0]);second['fill_id']='history-row-2';e['fills'].append(second)
    c['transactions'].insert(1,deepcopy(c['transactions'][0]))
    result=compare_sources(e,p,c)
    assert len(result['paired_fills'])==2 and result['checks']['all_partial_fills_paired']
    assert len({r['export_fill_id'] for r in result['paired_fills']})==2
    c['transactions'].pop(1)
    assert not compare_sources(e,p,c)['checks']['all_partial_fills_paired']


def test_different_full_precision_fills_in_same_display_bin_are_ambiguous(evidence):
    e,p,c=evidence
    second=deepcopy(e['fills'][0]);second.update(fill_id='history-row-2',price_usd='10000.12999');e['fills'].append(second)
    c['transactions'].insert(1,deepcopy(c['transactions'][0]))
    result=compare_sources(e,p,c)
    assert result['ambiguous_fill_matches'] and not result['checks']['all_partial_fills_paired']


@pytest.mark.parametrize('change',['funding','coverage','quantity','ending_cash','unknown_cash','market_value','ledger_match'])
def test_other_source_controls_remain_independent_and_fail_closed(evidence,change):
    e,p,c=evidence
    if change=='funding':e['cash_ledger'][1]['amount_usd']='6.35'
    elif change=='coverage':e['export_end']='2026-10-30'
    elif change=='quantity':c['holdings']['BTC/USD']['quantity']='0.012'
    elif change=='ending_cash':e['last_reported_cash_usd']='0.01'
    elif change=='unknown_cash':e['cash_ledger'][0]['type']='OTHER'
    elif change=='market_value':c['holdings']['BTC/USD']['market_value_usd']='109.99'
    else:e['fill_control_reconciled']=False
    result=compare_sources(e,p,c)
    assert not result['all_matched_evidence_checks_pass'] and result['review_required']
    assert result['publication_authorized'] is False


@pytest.mark.parametrize('value',['NaN','Infinity'])
def test_nonfinite_values_rejected(evidence,value):
    evidence[0]['fills'][0]['principal_usd']=value
    with pytest.raises(ValueError):compare_sources(*evidence)


def test_cli_missing_files_returns_private_failure_with_no_authority(tmp_path):
    missing=str(tmp_path/'absent')
    result=subprocess.run([sys.executable,'-m','campaigniq.crypto_source_review','--development-only',
        '--trade-history',missing,'--previous-statement',missing,'--current-statement',missing,'--year','2026','--month','10'],capture_output=True,text=True)
    assert result.returncode==1 and json.loads(result.stdout)['publication_authorized'] is False
    assert not list(tmp_path.iterdir())


def test_review_requires_three_distinct_originals_before_pdf_extraction(tmp_path):
    path=tmp_path/'source';path.write_bytes(b'%PDF-test')
    with pytest.raises(ValueError,match='distinct'):review_files(path,path,path,year=2026,month=10)
    assert path.read_bytes()==b'%PDF-test'
