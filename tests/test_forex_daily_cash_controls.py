from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
import importlib.util
import json
import re
import pytest
from campaigniq.forex_daily_cash_resolution import resolve_daily_cash_statement, POLICY
from campaigniq.forex_opening_statement import read_forex_opening_statement, attach_forex_opening
from campaigniq.importers.schwab.forex_transaction_reader import read_forex_transaction_report
from campaigniq.domain.lot_book import LotBook
from campaigniq.import_preflight import MonthlyImportPreflight
from campaigniq.import_contract import monthly_import_contract, MonthlyInputRole as Role
from campaigniq.import_validation import MonthlyInputValidation
from campaigniq.monthly_import_execution import execute_monthly_import, MonthlyImportExecution
from campaigniq.closing_inventory_reconciliation import ClosingInventoryReconciliation
from campaigniq.persistence.forex_settlement_attribution_store import deserialize_forex_settlement_attributions

SOURCE=Path(__file__).parent/'fixtures'/'forex_cash_controls'


@pytest.fixture(params=[('mar',3,31),('nov',11,30),('dec',12,31)])
def evidence(request,tmp_path):
    name,month,last=request.param
    for suffix in ('history.csv','report.csv','monthly.txt'):
        (tmp_path/suffix).write_bytes((SOURCE/(name+'-'+suffix)).read_bytes())
    return name,tmp_path,date(2025,month,last)


def resolve(evidence,text=None):
    _,root,end=evidence
    return resolve_daily_cash_statement(text if text is not None else (root/'monthly.txt').read_text(),trade_history=root/'history.csv',transaction_report=root/'report.csv',period_end=end)


def test_actual_month_controls_and_original_discrepancies(evidence):
    name,p,end=evidence;before={f:f.read_bytes() for f in p.iterdir()}
    r,a=resolve(evidence)
    values={'mar':('439.35','439.35','78.14','0',7),'nov':('61.38','61.38','-19.72','0',20),'dec':('-912.33','-939.65','-26.20','27.31',27)}
    trade,cash,finance,pending,count=values[name]
    assert r.settlement_pl_usd==Decimal(trade) and r.mtd_settled_pl_usd==Decimal(cash)
    assert r.financing_usd==Decimal(finance) and r.pending_settlement_pl_usd==Decimal(pending)
    assert len(r.settlements)==count and r.source_policy==POLICY
    assert r.settlement_control_reconciled and r.pl_total_control_reconciled and r.financing_control_reconciled and r.commission_control_reconciled
    assert a['csv_financing_outside_monthly_scope'] and a['settlement_changes']
    assert a['timestamp_differences']
    assert before=={f:f.read_bytes() for f in p.iterdir()}
    assert read_forex_transaction_report(p/'report.csv').source_policy is None
    assert all(abs(Decimal(v))<=Decimal('.01') for v in a['aggregate_differences_usd'].values())


def test_no_cash_bridge_without_explicit_pending_amount(evidence):
    r,a=resolve(evidence)
    if evidence[0]=='dec':
        assert not replace(r,pending_settlement_pl_usd=Decimal(0)).settlement_control_reconciled
        missing=next(s for s in r.settlements if s.order_id=='1004840489728')
        assert missing.settlement_at is None and missing.settlement_pl_usd==Decimal('-1008.60')
        assert a['missing_csv_closes']==['1004840489728']
        assert next(s for s in r.settlements if s.order_id=='1004903592654').settlement_pl_usd==Decimal('-3.92')
        assert next(d for d in a['settlement_changes'] if d['reference']=='1004903592654')['history_usd']=='-3.91'
    elif evidence[0]=='nov':
        assert next(s for s in r.settlements if s.order_id=='1004635288210').amount==Decimal(10000)
        assert next(s for s in r.new_transactions if s.order_id=='1004635288210').amount==Decimal(10000)
        assert a['zero_closes_omitted_from_daily_dollar_table']==['1004760537161']
    else:
        assert a['original_csv_controls']['mtd_pl_usd']=='0.00'
        assert a['original_csv_controls']['period_end']=='2025-04-01'


@pytest.mark.parametrize('label',['Settled Profit/Loss','Financing','Ending Cash Balance'])
def test_changed_monthly_control_blocks(evidence,label):
    text=(evidence[1]/'monthly.txt').read_text()
    changed=re.sub(r'('+label+r'\s+)(\(?\$[\d,.]+\)?)',r'\g<1>$999999.00',text,count=1)
    assert changed!=text
    with pytest.raises(ValueError):resolve(evidence,changed)


def test_changed_account_blocks(evidence):
    text=(evidence[1]/'monthly.txt').read_text().replace('1234567','7654321')
    with pytest.raises(ValueError,match='account'):resolve(evidence,text)


def test_changed_execution_rate_is_never_repaired(evidence):
    text=(evidence[1]/'monthly.txt').read_text()
    from campaigniq.forex_opening_statement import _TRADE
    line=next(line for line in text.splitlines() if _TRADE.fullmatch(line))
    m=_TRADE.fullmatch(line);changed=line[:m.start(6)]+str(Decimal(m[6])+Decimal('.000001'))+line[m.end(6):]
    with pytest.raises(ValueError):resolve(evidence,text.replace(line,changed,1))


def test_duplicate_execution_blocks(evidence):
    text=(evidence[1]/'monthly.txt').read_text()
    from campaigniq.forex_opening_statement import _TRADE
    line=next(line for line in text.splitlines() if _TRADE.fullmatch(line))
    with pytest.raises(ValueError):resolve(evidence,text.replace(line,line+'\n'+line,1))


def test_broken_daily_coverage_blocks(evidence):
    text=(evidence[1]/'monthly.txt').read_text()
    # Keep summary and opening page; omit an internal complete daily report.
    pages=[p for p in re.split(r'(?=^Customer Account Summary Report since )',text.replace('\f','\n'),flags=re.M) if p.strip()]
    with pytest.raises(ValueError):resolve(evidence,''.join(pages[:3]+pages[4:]))


def test_large_cash_difference_has_no_new_waiver(evidence):
    r,_=resolve(evidence)
    assert not replace(r,mtd_settled_pl_usd=r.mtd_settled_pl_usd+Decimal(1)).settlement_control_reconciled
    assert not replace(r,source_policy=None,mtd_settled_pl_usd=r.settlement_pl_usd+Decimal('.01')).settlement_control_reconciled


def execute(evidence,preview=True):
    _,p,end=evidence
    supplied={Role.THINKORSWIM_TRADE_HISTORY:p/'history.csv',Role.SCHWAB_FOREX_TRANSACTION_REPORT:p/'report.csv',Role.SCHWAB_FOREX_MONTHLY_STATEMENT:p/'monthly.txt'}
    empty=p/'empty.txt';empty.write_text('')
    supplied.update({Role.SCHWAB_REALIZED_GAIN_LOSS:empty,Role.SCHWAB_CLOSING_POSITION_SNAPSHOT:empty})
    proof=read_forex_opening_statement((p/'monthly.txt').read_text(),trade_history=p/'history.csv',transaction_report=p/'report.csv',period_end=end)
    book=attach_forex_opening(LotBook(),proof,period_start=end.replace(day=1),authoritative=False)
    contract=monthly_import_contract(2025,end.month)
    preflight=MonthlyImportPreflight(contract,tuple(MonthlyInputValidation(r.role,True,'fixture') for r in contract.requirements),None,book,True,False)
    return execute_monthly_import(preflight,authoritative_state_root=p/'state',supplied_inputs=supplied,preview_only=preview)


def test_pipeline_preview_attributes_all_corrected_closes_without_writes(evidence):
    r=execute(evidence)
    assert not r.finalized and not (evidence[1]/'state').exists()
    assert r.closing_reconciliation.reconciled and not r.closing_reconciliation.forex_source_findings
    assert r.forex_resolution['policy']==POLICY
    assert len(r.result.forex_settlement_attributions)==len(r.result.forex_transaction_report.settlements)
    assert sum((a.gain_loss for a in r.result.forex_settlement_attributions),Decimal(0))==r.result.forex_transaction_report.settlement_pl_usd


def test_finalized_resolution_preserves_financial_audit_and_pending_bridge(evidence):
    _,p,end=evidence;r=execute(evidence,False)
    assert r.finalized
    audit=json.loads((p/'state'/f'{end:%Y-%m}-forex-source-resolution.json').read_text())
    assert audit['accounting_basis'].startswith('Trade-date') and audit['source_sha256']
    assert audit['pending_settlement_pl_usd']==str(r.result.forex_transaction_report.pending_settlement_pl_usd)
    stored=deserialize_forex_settlement_attributions((p/'state'/f'{end:%Y-%m}-forex-settlement-attributions.json').read_text())
    assert stored.attributions==r.result.forex_settlement_attributions


def test_bad_financial_source_cannot_finalize(evidence):
    p=evidence[1];path=p/'monthly.txt';text=path.read_text()
    text=re.sub(r'(Settled Profit/Loss\s+)(\(?\$[\d,.]+\)?)',r'\g<1>$999999.00',text,count=1)
    path.write_text(text)
    preview=execute(evidence,True)
    assert preview.forex_resolution is None and preview.closing_reconciliation.forex_source_findings
    assert not (p/'state').exists()
    with pytest.raises(ValueError):execute(evidence,False)
    assert not (p/'state').exists()


def test_other_year_cannot_activate_policy(evidence):
    _,p,_=evidence
    with pytest.raises(ValueError,match='limited'):
        resolve_daily_cash_statement((p/'monthly.txt').read_text(),trade_history=p/'history.csv',transaction_report=p/'report.csv',period_end=date(2026,12,31))


def test_exact_attribution_refuses_missing_or_ambiguous_campaign(evidence):
    from campaigniq.forex_daily_cash_resolution import attribute_daily_cash_settlements
    from types import SimpleNamespace
    r=execute(evidence)
    with pytest.raises(ValueError,match='exact history-fill'):
        attribute_daily_cash_settlements((),r.result.forex_transaction_report.settlements,evidence[1]/'history.csv')
    campaigns=r.result.campaigns
    duplicate=tuple(SimpleNamespace(campaign_id=c.campaign_id+'-other',trades=c.trades) for c in campaigns)
    with pytest.raises(ValueError,match='exact history-fill'):
        attribute_daily_cash_settlements((*campaigns,*duplicate),r.result.forex_transaction_report.settlements,evidence[1]/'history.csv')


def test_extra_two_second_clock_difference_is_not_accepted(evidence):
    name,p,end=evidence;path=p/'report.csv';text=path.read_text()
    raw=read_forex_transaction_report(path);event=(*raw.settlements,*raw.new_transactions)[0]
    from datetime import timedelta
    before=event.trade_at.strftime('%b %d, %Y %H:%M:%S').replace(' 0',' ')
    after=(event.trade_at+timedelta(seconds=3)).strftime('%b %d, %Y %H:%M:%S').replace(' 0',' ')
    assert before in text
    path.write_text(text.replace(before,after,1))
    with pytest.raises(ValueError):resolve(evidence)

