from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import json
import pytest
from campaigniq.importers.thinkorswim.crypto_reader import read_crypto_report
from campaigniq.persistence.crypto_month import build_crypto_month, load_preceding_crypto, serialize_crypto_month
from campaigniq.persistence.artifact_storage import LocalFilesystemArtifactStorage
from campaigniq.persistence.monthly_publication import ensure_publication_protocol_in_storage, publish_finalized_month_marker_to_storage, unpublish_finalized_month_marker_from_storage, is_month_published_in_storage
START, END = date(2026,9,1), date(2026,9,30)
EXPORT = '''Account Statement for synthetic account since 8/31/26 through 9/29/26

Cash Balance
DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE

Account Trade History
,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type
,9/28/26 10:22:10,CRYPTO,BUY,+0.00587022,TO OPEN,BTC/USD,,,CRYPTO,83703.30,83703.30,LMT
,9/28/26 10:22:10,CRYPTO,BUY,+0.00005821,TO OPEN,BTC/USD,,,CRYPTO,83703.30,83703.30,LMT
,9/28/26 10:22:10,CRYPTO,BUY,+0.00000057,TO OPEN,BTC/USD,,,CRYPTO,83703.30,83703.30,LMT

Forex Statements
Trade Date,Exec Date,Exec Time,Type,Ref #,Description,Commissions & Fees,Amount,Balance

"Crypto #SYNTHETIC (Crypto offered by test bank) Statements"
Trade Date,Exec Date,Exec Time,Type,Ref #,Description,Commissions & Fees,Amount,Balance
8/31/26,8/31/26,00:00:00,BAL,,Cash Balance,--,--,0.00
9/27/26,9/28/26,10:20:00,FND,1,Funding,--,500.00,500.00
9/27/26,9/28/26,10:21:00,FND,2,Funding,--,3.75,503.75
9/27/26,9/28/26,10:22:10,TRD,100,BOT 0.00587022 BTC/USD @83703.30,-3.68,-491.36,8.71
9/27/26,9/28/26,10:22:10,TRD,100,BOT 0.00005821 BTC/USD @83703.30,-0.04,-4.87,3.80
9/27/26,9/28/26,10:22:10,TRD,100,BOT 0.00000057 BTC/USD @83703.30,--,-0.05,3.75
9/29/26,9/29/26,00:00:00,BAL,,Cash Balance,--,--,3.75

"Crypto #SYNTHETIC (Crypto offered by test bank)"
Symbol,Qty,Trade Price,Total Cost,P/L Open,P/L Day,Net Liq
BTC/USD,+0.005929,83703.8286,.00,($0.86),$0.62,$495.42

'''
def read(tmp_path, text=EXPORT):
    path=tmp_path/'export.csv'; path.write_text(text)
    return read_crypto_report(path, period_start=START, period_end=END)

def test_partial_fills_exact_cost_cash_and_dates(tmp_path):
    raw=read(tmp_path); report=build_crypto_month(raw)
    assert len(report['fills'])==len({r['fill_id'] for r in report['fills']})==3
    assert {r['reference'] for r in report['fills']}=={'100'}
    assert Decimal(report['ending_quantities']['BTC/USD'])==Decimal('.00592900')
    assert sum(Decimal(r['cost_usd']) for r in report['ending_lots']['BTC/USD'])==500
    assert sum(Decimal(r['fee_usd']) for r in report['fills'])==Decimal('3.72')
    assert report['last_reported_cash_usd']=='3.75'
    assert report['net_funding_usd']=='503.75' and report['realized_pnl_usd']=='0'
    assert report['cash_control_reconciled'] and report['fill_control_reconciled']
    assert report['snapshot_quantity_matches'] and not report['month_end_reconciled']
    assert report['fills'][0]['trade_date']=='2026-09-27'
    assert report['fills'][0]['executed_at'].startswith('2026-09-28')
    assert any('entire selected month' in w for w in report['warnings'])
    assert 'ending_lots' not in raw
    assert json.loads(serialize_crypto_month(report))==report

def test_missing_ledger_fee_is_unknown(tmp_path):
    raw=read(tmp_path, EXPORT.replace('9/27/26,9/28/26,10:22:10,TRD,100,BOT 0.00000057 BTC/USD @83703.30,--,-0.05,3.75\n',''))
    assert not raw['fill_control_reconciled'] and not raw['cash_control_reconciled']
    assert raw['fills'][-1]['fee_usd'] is None
    assert build_crypto_month(raw)['ending_lots']['BTC/USD'][-1]['cost_usd'] is None

def test_inferred_opening_inventory_unknown_cost(tmp_path):
    report=build_crypto_month(read(tmp_path,EXPORT.replace('+0.005929,','+0.105929,')))
    assert Decimal(report['opening_quantities']['BTC/USD'])==Decimal('.1')
    assert report['ending_lots']['BTC/USD'][0]['cost_usd'] is None

def sale_report(previous, qty='.00293511'):
    raw=deepcopy(previous)
    raw.update(period_start='2026-10-01',period_end='2026-10-31',opening_cash_usd='3.75')
    raw['fills']=[dict(fill_id='history-row-1',executed_at='2026-10-02T12:00:00',pair='BTC/USD',side='SELL',quantity=qty,principal_usd='300',fee_usd='1')]
    raw['holdings_snapshot']['BTC/USD'].update(quantity='.002994',quantity_precision='.000001')
    return raw

def test_carried_fifo_sale(tmp_path):
    previous=build_crypto_month(read(tmp_path)); result=build_crypto_month(sale_report(previous),previous)
    sale=result['realized_sales'][0]
    assert Decimal(sale['fifo_cost_usd'])==Decimal('247.52')
    assert Decimal(sale['pnl_usd'])==Decimal('51.48')
    assert result['snapshot_quantity_matches']
    assert Decimal(previous['ending_lots']['BTC/USD'][0]['quantity'])==Decimal('.00587022')

def test_unknown_opening_sale_has_unknown_pnl(tmp_path):
    previous=build_crypto_month(read(tmp_path,EXPORT.replace('+0.005929,','+0.105929,')))
    result=build_crypto_month(sale_report(previous,'.02'),previous)
    assert result['realized_pnl_usd'] is None
    assert result['realized_sales'][0]['fifo_cost_usd'] is None

def test_account_change_refused(tmp_path):
    raw=read(tmp_path); previous=build_crypto_month(raw); raw['account']='other'
    with pytest.raises(ValueError,match='account changed'): build_crypto_month(raw,previous)

@pytest.mark.parametrize('old,new', [('BUY,+0.00587022','BUY,-0.00587022'),('CRYPTO,83703.30,','CRYPTO,NaN,'),('BTC/USD','BTC/EUR'),('--,-0.05,3.75','--,0.05,3.75')])
def test_invalid_evidence_refused(tmp_path,old,new):
    with pytest.raises((ValueError,ArithmeticError)): read(tmp_path,EXPORT.replace(old,new))

def test_period_filter_by_execution(tmp_path):
    path=tmp_path/'export.csv';path.write_text(EXPORT)
    raw=read_crypto_report(path,period_start=date(2026,10,1),period_end=date(2026,10,31))
    assert raw['fills']==[] and raw['cash_ledger']==[]

def test_regular_reader_excludes_crypto(tmp_path):
    from campaigniq.importers.thinkorswim.trade_reader import ThinkorswimTradeReader
    from campaigniq.importers.thinkorswim.trade_history_reader import ThinkorswimTradeHistoryReader
    from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader
    path=tmp_path/'export.csv';path.write_text(EXPORT)
    assert ThinkorswimTradeReader(ThinkorswimSourceReader(),ThinkorswimTradeHistoryReader()).read(path,start=START,end=END)==[]

def test_crypto_only_validation(tmp_path):
    from campaigniq.import_validation import validate_monthly_input
    from campaigniq.import_contract import MonthlyInputRole
    path=tmp_path/'export.csv';path.write_text(EXPORT)
    result=validate_monthly_input(MonthlyInputRole.THINKORSWIM_TRADE_HISTORY,path,period_start=START,period_end=END)
    assert result.valid and '3 crypto fills' in result.message and result.record_count==0

def test_history_publication_and_workspace_isolation(tmp_path):
    raw=build_crypto_month(read(tmp_path));storage=LocalFilesystemArtifactStorage(tmp_path/'a')
    ensure_publication_protocol_in_storage(storage,first_period_end=END)
    storage.write_text('2026-09-crypto.json',serialize_crypto_month(raw))
    assert load_preceding_crypto(storage,date(2026,10,1)) is None
    publish_finalized_month_marker_to_storage(storage,period_end=END)
    assert load_preceding_crypto(storage,date(2026,10,1))==raw
    assert load_preceding_crypto(storage,date(2026,11,1)) is None
    assert load_preceding_crypto(LocalFilesystemArtifactStorage(tmp_path/'b'),date(2026,10,1)) is None
    unpublish_finalized_month_marker_from_storage(storage,period_end=END)
    assert load_preceding_crypto(storage,date(2026,10,1)) is None

def setup_execution(tmp_path,monkeypatch,raw):
    import campaigniq.monthly_import_execution as module
    from campaigniq.domain.lot_book import LotBook
    from campaigniq.import_contract import monthly_import_contract
    from campaigniq.import_preflight import MonthlyImportPreflight
    from campaigniq.import_validation import MonthlyInputValidation
    from campaigniq.closing_inventory_reconciliation import ClosingInventoryReconciliation
    contract=monthly_import_contract(2026,9)
    preflight=MonthlyImportPreflight(contract,tuple(MonthlyInputValidation(r.role,True,'test') for r in contract.requirements),None,LotBook())
    inputs={}
    for r in contract.user_supplied_requirements:
        path=tmp_path/(r.role.value+'.txt');path.write_text('synthetic evidence\n');inputs[r.role]=path
    result=SimpleNamespace(ending_lot_book=LotBook(),forex_settlement_attributions=(),lifecycle_transitions=(),crypto_report=raw,boundary_reconstruction=SimpleNamespace(unresolved_positions=(),unresolved_campaigns=(),historical_requirements=()))
    monkeypatch.setattr(module,'PeriodImportPipeline',lambda:SimpleNamespace(run=lambda **kwargs:result))
    monkeypatch.setattr(module,'read_position_snapshot_section',lambda *args,**kwargs:())
    monkeypatch.setattr(module,'read_pending_position_activity',lambda *args,**kwargs:())
    monkeypatch.setattr(module,'reconcile_closing_inventory',lambda **kwargs:ClosingInventoryReconciliation(()))
    monkeypatch.setattr(module,'attribute_period_realized_pnl',lambda result:())
    return module,preflight,inputs,result

def test_publication_and_stale_crypto_removal(tmp_path,monkeypatch):
    module,preflight,inputs,result=setup_execution(tmp_path,monkeypatch,read(tmp_path))
    storage=LocalFilesystemArtifactStorage(tmp_path/'state')
    outcome=module.execute_monthly_import(preflight,authoritative_state_root=storage.root,supplied_inputs=inputs,artifact_storage=storage)
    assert outcome.finalized and json.loads(storage.read_text('2026-09-crypto.json'))==outcome.crypto_report
    assert is_month_published_in_storage(storage,period_end=END)
    result.crypto_report=None
    outcome=module.execute_monthly_import(preflight,authoritative_state_root=storage.root,supplied_inputs=inputs,artifact_storage=storage)
    assert outcome.finalized and not storage.exists('2026-09-crypto.json')

def test_write_failure_cannot_publish(tmp_path,monkeypatch):
    module,preflight,inputs,result=setup_execution(tmp_path,monkeypatch,read(tmp_path))
    class FailingStorage(LocalFilesystemArtifactStorage):
        def write_text(self,key,content):
            if key.endswith('-crypto.json'):raise OSError('simulated failure')
            super().write_text(key,content)
    storage=FailingStorage(tmp_path/'state')
    with pytest.raises(OSError,match='simulated'):
        module.execute_monthly_import(preflight,authoritative_state_root=storage.root,supplied_inputs=inputs,artifact_storage=storage)
    assert not is_month_published_in_storage(storage,period_end=END)

def test_ui_exact_fill_quantities(tmp_path):
    from campaigniq.ui.crypto_view import render_crypto_report
    class UI:
        def __init__(self):self.tables=[];self.messages=[]
        def columns(self,n):return [self]*n
        def dataframe(self,rows,**kwargs):self.tables.append(rows)
        def expander(self,*args,**kwargs):return self
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def __getattr__(self,name):return lambda *args,**kwargs:self.messages.append((name,args))
    ui=UI();render_crypto_report(build_crypto_month(read(tmp_path)),ui)
    fills=next(t for t in ui.tables if t and 'Quantity' in t[0])
    assert len(fills)==3 and Decimal(fills[-1]['Quantity'])==Decimal('.00000057')
    assert any('provisional' in str(args) for name,args in ui.messages)

def test_real_pipeline_preserves_crypto_separately(tmp_path):
    from campaigniq.import_pipeline import PeriodImportPipeline
    from campaigniq.domain.lot_book import LotBook
    path=tmp_path/'export.csv';path.write_text(EXPORT)
    result=PeriodImportPipeline().run(period_start=START,period_end=END,thinkorswim_trade_history=path,carried_opening_lot_book=LotBook())
    assert len(result.crypto_report['fills'])==3
    assert result.trades==()


def test_malformed_crypto_amount_validation_is_readable(tmp_path):
    from campaigniq.import_validation import validate_monthly_input
    from campaigniq.import_contract import MonthlyInputRole
    path=tmp_path/'export.csv';path.write_text(EXPORT.replace('+0.00587022','broken'))
    result=validate_monthly_input(MonthlyInputRole.THINKORSWIM_TRADE_HISTORY,path,period_start=START,period_end=END)
    assert not result.valid
