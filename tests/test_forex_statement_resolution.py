"""Combined August/September financial resolution and durable provenance."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
import json
import pytest
from campaigniq.forex_statement_resolution import resolve_forex_statement,POLICY
from campaigniq.forex_opening_statement import read_forex_opening_statement,attach_forex_opening
from campaigniq.importers.schwab.forex_transaction_reader import read_forex_transaction_report
from campaigniq.import_preflight import MonthlyImportPreflight
from campaigniq.import_contract import monthly_import_contract,MonthlyInputRole as Role
from campaigniq.import_validation import MonthlyInputValidation
from campaigniq.domain.lot_book import LotBook
from campaigniq.monthly_import_execution import execute_monthly_import
from campaigniq.persistence.forex_settlement_attribution_store import deserialize_forex_settlement_attributions

ROOT=Path(__file__).parent/'fixtures/forex_resolution'

@pytest.fixture(params=['aug','sep'])
def evidence(request,tmp_path):
    name=request.param
    for suffix in ('monthly.txt','history.csv','report.csv'):
        (tmp_path/suffix).write_bytes((ROOT/(name+'-'+suffix)).read_bytes())
    return name,tmp_path,date(2025,8,31) if name=='aug' else date(2025,9,30)


def run(evidence,text=None):
    _,p,end=evidence
    return resolve_forex_statement(text if text is not None else (p/'monthly.txt').read_text(),trade_history=p/'history.csv',transaction_report=p/'report.csv',period_end=end)


def test_money_financing_inventory_controls_and_source_bytes(evidence):
    name,p,end=evidence;before={f:f.read_bytes() for f in p.iterdir()}
    report,audit=run(evidence)
    assert report.source_policy==POLICY
    assert report.settlement_pl_usd==Decimal('-3040.50' if name=='aug' else '-4638.00')
    assert report.financing_usd==Decimal('77.74' if name=='aug' else '170.42')
    assert report.settlement_control_reconciled and report.pl_total_control_reconciled
    assert report.financing_control_reconciled
    assert len(report.settlements)==(10 if name=='aug' else 1)
    assert sum(s.settlement_at is None for s in report.settlements)==(3 if name=='aug' else 1)
    assert all(s.evidence_source for s in report.settlements)
    assert all(abs(Decimal(x))<=Decimal('.01') for x in audit['aggregate_differences_usd'].values())
    assert before=={f:f.read_bytes() for f in p.iterdir()}
    assert read_forex_transaction_report(p/'report.csv').source_policy is None


def test_resolution_keeps_original_csv_disagreements_and_financing_membership(evidence):
    name,p,end=evidence;report,audit=run(evidence)
    assert len(audit['settlement_changes'])==(5 if name=='aug' else 1)
    assert len(audit['csv_financing_outside_monthly_scope'])==2
    assert audit['monthly_financing_absent_from_csv']==([] if name=='aug' else ['102122010699'])
    assert ('102122010699' in {f.order_id for f in report.financing})==(name=='sep')
    assert {f.instrument for f in report.financing}=={'USD$FX'}


@pytest.mark.parametrize('label', ['Settled Profit/Loss','Financing','Ending Cash Balance'])
def test_changed_summary_amount_blocks_instead_of_forcing(evidence,label):
    name,p,end=evidence;text=(p/'monthly.txt').read_text()
    import re
    text=re.sub(r'('+label+r'\s+)(\(?\$[\d,.]+\)?)',r'\g<1>$999999.00',text,count=1)
    with pytest.raises(ValueError):run(evidence,text)


def test_missing_monthly_execution_blocks(evidence):
    name,p,end=evidence;text=(p/'monthly.txt').read_text()
    from campaigniq.forex_opening_statement import _TRADE
    target=next(l for l in text.splitlines() if _TRADE.fullmatch(l))
    with pytest.raises(ValueError):run(evidence,text.replace(target,''))


def test_changed_history_close_amount_blocks_pair_control(evidence):
    name,p,end=evidence;path=p/'history.csv';text=path.read_text()
    text=text.replace('($1,179.00)','($1,180.00)') if name=='aug' else text.replace('($4,638.00)','($4,639.00)')
    assert text!=path.read_text();path.write_text(text)
    with pytest.raises(ValueError):run(evidence)


def test_unsupported_month_does_not_activate_policy(evidence):
    _,p,end=evidence
    with pytest.raises(ValueError,match='limited'):
        resolve_forex_statement((p/'monthly.txt').read_text(),trade_history=p/'history.csv',transaction_report=p/'report.csv',period_end=date(2025,10,31))


def execute(evidence,preview):
    name,p,end=evidence;monthly=(p/'monthly.txt').read_text()
    proof=read_forex_opening_statement(monthly,trade_history=p/'history.csv',transaction_report=p/'report.csv',period_end=end)
    book=attach_forex_opening(LotBook(),proof,period_start=end.replace(day=1),authoritative=False)
    empty=p/'empty.txt';empty.write_text('')
    supplied={Role.THINKORSWIM_TRADE_HISTORY:p/'history.csv',Role.SCHWAB_FOREX_TRANSACTION_REPORT:p/'report.csv',Role.SCHWAB_FOREX_MONTHLY_STATEMENT:p/'monthly.txt',Role.SCHWAB_REALIZED_GAIN_LOSS:empty,Role.SCHWAB_CLOSING_POSITION_SNAPSHOT:empty}
    contract=monthly_import_contract(2025,end.month)
    preflight=MonthlyImportPreflight(contract,tuple(MonthlyInputValidation(q.role,True,'fixture') for q in contract.requirements),None,book,True,False)
    return execute_monthly_import(preflight,authoritative_state_root=p/'state',supplied_inputs=supplied,preview_only=preview)


def test_combined_preview_attributes_every_close_and_writes_nothing(evidence):
    name,p,end=evidence
    result=execute(evidence,True)
    assert not (p/'state').exists()
    assert result.closing_reconciliation.reconciled
    assert not result.closing_reconciliation.forex_source_findings
    assert len(result.result.forex_settlement_attributions)==(10 if name=='aug' else 1)
    assert sum(a.gain_loss for a in result.result.forex_settlement_attributions)==Decimal('-3040.50' if name=='aug' else '-4638.00')
    assert result.forex_resolution and not result.finalized


def test_finalization_persists_realized_money_null_dates_and_resolution_audit(evidence):
    name,p,end=evidence
    original=(p/'report.csv').read_bytes()
    result=execute(evidence,False)
    assert result.finalized
    audit=json.loads((p/'state'/f'{end:%Y-%m}-forex-source-resolution.json').read_text())
    assert audit['policy']==POLICY
    stored=deserialize_forex_settlement_attributions((p/'state'/f'{end:%Y-%m}-forex-settlement-attributions.json').read_text())
    assert stored.attributions==result.result.forex_settlement_attributions
    assert any(a.settlement.settlement_at is None for a in stored.attributions)
    assert (p/'report.csv').read_bytes()==original


def test_legacy_csv_has_no_new_cent_waiver(evidence):
    report,_=run(evidence)
    legacy=replace(report,source_policy=None,mtd_settled_pl_usd=report.settlement_pl_usd+Decimal('.01'))
    assert not legacy.settlement_control_reconciled


@pytest.mark.parametrize('kind',['period','account','finance_amount'])
def test_conflicting_original_csv_cannot_be_forced(evidence,kind):
    name,p,end=evidence;path=p/'report.csv';text=path.read_text()
    if kind=='period':text=text.replace('2025 16:00:00 (EDT)', '2024 16:00:00 (EDT)',1)
    elif kind=='account':text=text.replace('1234567','7654321')
    else:text=text.replace('+10.52','+10.53',1) if name=='sep' else text.replace('+10.49','+10.50',1)
    assert text!=path.read_text();path.write_text(text)
    with pytest.raises(ValueError):run(evidence)


def test_drilldown_uses_actual_close_date_without_inventing_settlement_date(evidence):
    from campaigniq.ui.campaign_interactions import forex_drilldowns
    result=execute(evidence,True);end=evidence[2]
    summaries,records=forex_drilldowns({(end.replace(day=1),end):result.result.forex_settlement_attributions})
    assert summaries
    assert all(s.first_closed_date is not None and s.last_closed_date is not None for s in summaries)
    assert any(a.settlement.settlement_at is None for attrs in records.values() for a in attrs)


def test_csv_execution_rate_conflict_blocks(tmp_path):
    for suffix in ('monthly.txt','history.csv','report.csv'):
        (tmp_path/suffix).write_bytes((ROOT/('aug-'+suffix)).read_bytes())
    path=tmp_path/'report.csv';path.write_text(path.read_text().replace('1.34725','1.34726',1))
    with pytest.raises(ValueError):run(('aug',tmp_path,date(2025,8,31)))
