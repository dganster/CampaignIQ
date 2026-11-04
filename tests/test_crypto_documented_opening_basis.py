from copy import deepcopy
from decimal import Decimal as D
import pytest

from campaigniq.crypto_statement_policy_preview import documented_opening_lots,build_preview,SOURCE_ROLES
from tests.test_crypto_source_review import evidence


@pytest.fixture
def documented(evidence):
    e,p,c=evidence
    deposit=p['transactions'][0];deposit['amount_usd']='104.75'
    rows=[]
    for quantity,cost,fee in (('0.006','60.60','0.60'),('0.003','30.30','0.30'),('0.001','10.10','0.10')):
        rows.append(dict(date='2026-09-28',action='BUY',pair='BTC/USD',quantity=quantity,price_usd='10000.00',fee_usd=fee,amount_usd='-'+cost))
    p['transactions']=[*rows,deposit]
    return evidence


def preview(evidence):return build_preview(*evidence,source_sha256={r:'a'*64 for r in SOURCE_ROLES}).to_dict()


def test_each_documented_partial_acquisition_supplies_cost_with_fees_once(documented):
    before=deepcopy(documented)
    lots,proof=documented_opening_lots(documented[1])
    assert len(lots['BTC/USD'])==3
    assert sum(D(l['quantity']) for l in lots['BTC/USD'])==D('.010')
    assert sum(D(l['cost_usd']) for l in lots['BTC/USD'])==D('101.00')
    assert proof['BTC/USD']['quantity_matches_closing_holding_exactly']
    assert proof['BTC/USD']['statement_transactions']==[0,1,2]
    assert {l['source'] for l in lots['BTC/USD']}=={'previous-statement-transaction-0','previous-statement-transaction-1','previous-statement-transaction-2'}
    body=preview(documented)
    assert body['opening_cost_basis_complete'] and body['ending_cost_basis_complete']
    assert D(body['totals']['documented_opening_cost_usd'])==D('101.00')
    assert D(body['totals']['full_ending_cost_basis_usd'])==D('111.11')
    assert body['review']['review_required'] and body['publication_authorized'] is False
    assert documented==before


def test_market_value_is_never_substituted_for_documented_acquisition_cash(documented):
    before=preview(documented)
    documented[1]['holdings']['BTC/USD']['market_value_usd']='90.00'
    after=preview(documented)
    assert before['totals']['documented_opening_cost_usd']==after['totals']['documented_opening_cost_usd']=='101.00'
    assert before['totals']['preceding_holdings_plus_cash_usd']!=after['totals']['preceding_holdings_plus_cash_usd']


@pytest.mark.parametrize('change',['missing_fill','one_satoshi','sale','unit_transfer'])
def test_incomplete_or_mixed_acquisition_evidence_keeps_basis_unknown(documented,change):
    p=documented[1]
    if change=='missing_fill':p['transactions'].pop(1)
    elif change=='one_satoshi':p['transactions'][0]['quantity']='0.00599999'
    elif change=='sale':p['transactions'][0]['action']='SELL'
    else:p['transactions'].append(dict(date='2026-09-29',action='DEPOSIT',pair='BTC/USD',quantity='0.001',price_usd='0',fee_usd='0',amount_usd='0'))
    lots,proof=documented_opening_lots(p)
    assert proof['BTC/USD']['status']=='unknown' and lots['BTC/USD'][0]['cost_usd'] is None


def test_current_sale_uses_documented_opening_cost_and_keeps_original_proof(documented):
    e,p,c=documented
    e['fills'][0]['side']='SELL'
    e['cash_ledger']=[];e['last_reported_cash_usd']='13.65'
    c['transactions']=[{**c['transactions'][0],'action':'SELL','amount_usd':'9.90'}]
    c['ending_cash_usd']='13.65';c['holdings']['BTC/USD'].update(quantity='0.009',market_value_usd='90.00')
    body=preview(documented)
    assert D(body['totals']['proposed_realized_sales_pnl_usd'])==D('-.20')
    assert D(body['fifo_allocations'][0]['cost_usd'])==D('10.10')
    proof=body['opening_basis']['BTC/USD']
    assert D(proof['acquisitions'][0]['quantity'])==D('.006')
    assert D(proof['acquisitions'][0]['cost_usd'])==D('60.60')
    assert D(body['ending_lots']['BTC/USD'][0]['quantity'])==D('.005')


@pytest.mark.parametrize('field,value',[('quantity','0'),('fee_usd','-1'),('amount_usd','1'),('amount_usd','-0.01')])
def test_invalid_acquisition_amounts_are_rejected(documented,field,value):
    documented[1]['transactions'][0][field]=value
    with pytest.raises(ValueError):documented_opening_lots(documented[1])
