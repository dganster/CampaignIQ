from datetime import date,datetime
from decimal import Decimal as D
from dataclasses import replace
import json
from campaigniq.domain.campaign_identity import separate_campaign_collisions,scope_import_campaigns,scope_legacy_opening_lots
from campaigniq.domain.campaign_realized_pnl import aggregate_campaign_realized_pnl
from campaigniq.analytics.campaign_drilldown_summary import summarize_campaign_drilldowns
from campaigniq.analytics.monthly_analytics_summary import summarize_monthly_analytics
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.persistence.realized_attribution_store import serialize_realized_attributions
from campaigniq.persistence.persisted_multi_month_analytics import load_persisted_monthly_attributions,load_persisted_monthly_attributions_from_storage
from campaigniq.persistence.artifact_storage import LocalFilesystemArtifactStorage
from campaigniq.ui.dashboard_campaigns import aggregate_period_qualified_campaigns,campaign_realized_attributions
START,END=date(2026,9,1),date(2026,9,30)

def attr(symbol,pnl,cid='CAMP-000002'):
    gain=D(pnl);basis=D('100')
    return RealizedAttribution(RealizedGainLossRecord(date(2026,9,25),Instrument(symbol),D('1'),D('1'),basis+gain,basis,gain,'FIFO','SHORT'),(LotAllocation(symbol,D('1'),basis,'TEST',cid),))


def test_same_month_reused_id_separates_symbols_and_keeps_money():
    records=(attr('ADP','1200'),attr('DE','-209.39'))
    results=aggregate_campaign_realized_pnl(records)
    assert {r.campaign_id for r in results}=={'CAMP-000002@ADP','CAMP-000002@DE'}
    assert sum(r.gain_loss for r in results)==D('990.61')
    assert {r.symbols for r in summarize_campaign_drilldowns(records)}=={('ADP',),('DE',)}
    assert all(r.fully_reconciled for r in results)
    assert records[0].campaign_ids==('CAMP-000002',)


def test_performance_counts_and_win_rate_use_separated_groups():
    summary=summarize_monthly_analytics(period_start=START,period_end=END,attributions=(attr('ADP','1200'),attr('DE','-209.39')))
    assert summary.realized_pnl.broker_realized_pnl==D('990.61')
    assert summary.campaign_performance.campaign_count==2
    assert summary.campaign_performance.winning_campaign_count==1
    assert summary.campaign_performance.losing_campaign_count==1
    assert summary.campaign_performance.win_rate==D('.5')


def test_drilldown_shows_only_records_for_selected_symbol():
    records=(attr('ADP','1200'),attr('DE','-209.39'))
    monthly={(START,END):records}
    results,details=aggregate_period_qualified_campaigns(monthly)
    for detail in details:
        actual=campaign_realized_attributions(monthly,detail.campaign_id)
        assert len(actual)==1 and actual[0].record.instrument.symbol==detail.symbols[0]
        assert actual[0].record.gain_loss==detail.realized_pnl
    assert sum(r.gain_loss for r in results)==D('990.61')


def test_no_collision_ids_unchanged_and_repeated_same_stock_grouped():
    records=(attr('DE','20'),attr('DE','30'))
    assert {r.campaign_id for r in aggregate_campaign_realized_pnl(records)}=={'CAMP-000002'}
    assert aggregate_campaign_realized_pnl(records)[0].gain_loss==50


def test_separation_idempotent():
    records=(attr('ADP','20'),attr('DE','30'))
    once=separate_campaign_collisions(records)
    assert separate_campaign_collisions(once)==once


def test_unknown_and_shared_provenance_stays_excluded():
    unknown=attr('DE','20',None)
    shared=replace(attr('ADP','30'),allocations=(LotAllocation('a',D('.5'),D('50'),'TEST','CAMP-000002'),LotAllocation('b',D('.5'),D('50'),'TEST','CAMP-000003')))
    assert aggregate_campaign_realized_pnl((unknown,shared))==()


def test_same_symbol_distinct_campaigns_remain_distinct():
    results=aggregate_campaign_realized_pnl((attr('DE','20','CAMP-000001'),attr('DE','30','CAMP-000002')))
    assert len(results)==2


def test_existing_published_storage_corrected_without_writing_or_reimport(tmp_path):
    records=(attr('ADP','1200'),attr('DE','-209.39'))
    text=serialize_realized_attributions(period_start=START,period_end=END,attributions=records)
    storage=LocalFilesystemArtifactStorage(tmp_path);storage.write_text('2026-09-realized-attributions.json',text)
    local=load_persisted_monthly_attributions((tmp_path/'2026-09-realized-attributions.json',))
    remote=load_persisted_monthly_attributions_from_storage(storage,('2026-09-realized-attributions.json',))
    assert local==remote and storage.read_text('2026-09-realized-attributions.json')==text
    assert {r.campaign_id for r in aggregate_campaign_realized_pnl(local[(START,END)])}=={'CAMP-000002@ADP','CAMP-000002@DE'}


def test_legacy_lots_scoped_without_mutating_original_or_inventing_date():
    book=LotBook();book.seed(Lot('old',Instrument('ADP'),D('1'),datetime(2026,8,31),D('100'),campaign_id='CAMP-000002'))
    scoped=scope_legacy_opening_lots(book)
    assert scoped.lots(Instrument('ADP'))[0].campaign_id=='LEGACY:ADP:CAMP-000002'
    assert book.lots(Instrument('ADP'))[0].campaign_id=='CAMP-000002'
    assert scope_legacy_opening_lots(scoped).lots(Instrument('ADP'))==scoped.lots(Instrument('ADP'))

EXPORT='''Account Statement for synthetic account since 8/31/26 through 9/30/26

Cash Balance
DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE

Account Trade History
,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type
,9/1/26 10:00:00,STOCK,SELL,-1,TO CLOSE,ADP,,,STOCK,120,120,LMT
,9/2/26 10:00:00,STOCK,BUY,+1,TO OPEN,DE,,,STOCK,100,100,LMT

Forex Statements
Trade Date,Exec Date,Exec Time,Type,Ref #,Description,Commissions & Fees,Amount,Balance

'''

def test_future_import_lots_do_not_reuse_prior_stock_campaign_id(tmp_path):
    from campaigniq.import_pipeline import PeriodImportPipeline
    path=tmp_path/'export.csv';path.write_text(EXPORT)
    prior=LotBook();prior.seed(Lot('old',Instrument('ADP'),D('1'),datetime(2026,8,31),D('100'),campaign_id='CAMP-000002'))
    result=PeriodImportPipeline().run(period_start=START,period_end=END,thinkorswim_trade_history=path,carried_opening_lot_book=prior,campaign_namespace='2026-09')
    assert result.opening_lot_book.lots(Instrument('ADP'))[0].campaign_id=='2026-09:ADP:CAMP-000001'
    assert result.ending_lot_book.lots(Instrument('DE'))[0].campaign_id=='2026-09:DE:CAMP-000002'
    assert prior.lots(Instrument('ADP'))[0].campaign_id=='CAMP-000002'
    ids={c.campaign_id for c in result.campaigns}
    assert ids=={'2026-09:ADP:CAMP-000001','2026-09:DE:CAMP-000002'}


def test_new_id_survives_carry_forward_and_different_month_is_unique(tmp_path):
    from campaigniq.import_pipeline import PeriodImportPipeline
    path=tmp_path/'export.csv';path.write_text(EXPORT)
    prior=LotBook();prior.seed(Lot('old',Instrument('ADP'),D('1'),datetime(2026,8,31),D('100'),campaign_id='2026-08:ADP:CAMP-000002'))
    prior.seed(Lot('untouched',Instrument('OTHER'),D('1'),datetime(2026,8,31),D('100'),campaign_id='2026-08:OTHER:CAMP-000002'))
    result=PeriodImportPipeline().run(period_start=START,period_end=END,thinkorswim_trade_history=path,carried_opening_lot_book=prior,campaign_namespace='2026-09')
    assert result.opening_lot_book.lots(Instrument('OTHER'))[0].campaign_id=='2026-08:OTHER:CAMP-000002'
    assert result.ending_lot_book.lots(Instrument('OTHER'))[0].campaign_id=='2026-08:OTHER:CAMP-000002'
    assert {c.campaign_id for c in scope_import_campaigns(result.campaigns,'2026-10')}.isdisjoint({c.campaign_id for c in result.campaigns})


def test_campaign_table_numeric_values_show_grouping_commas():
    from pathlib import Path
    import pandas as pd
    value=pd.DataFrame([{'Realized P&L':-6638.05}])
    styled=value.style.format({'Realized P&L':'{:,.2f}'})
    assert '-6,638.05' in styled.to_html()
    assert value['Realized P&L'].dtype.kind=='f'
    source=Path('src/campaigniq/ui/dashboard.py').read_text()
    interactions=Path("src/campaigniq/ui/campaign_interactions.py").read_text()
    assert 'style.format({"Realized P&L": "{:,.2f}"})' in interactions
    assert "render_campaign_table(" in source

def test_stock_and_options_for_same_underlying_stay_one_campaign():
    from campaigniq.domain.option_contract import OptionContract
    from campaigniq.domain.option_type import OptionType
    stock=attr('ADP','20')
    option=replace(attr('ADP','30'),record=replace(attr('ADP','30').record,instrument=OptionContract('ADP',date(2026,10,2),D('140'),OptionType.PUT)))
    results=aggregate_campaign_realized_pnl((stock,option))
    assert len(results)==1 and results[0].gain_loss==50
    assert summarize_campaign_drilldowns((stock,option))[0].symbols==('ADP',)


def test_monthly_execution_requests_scoped_ids(tmp_path,monkeypatch):
    import campaigniq.monthly_import_execution as module
    from campaigniq.import_contract import monthly_import_contract
    from campaigniq.import_preflight import MonthlyImportPreflight
    from campaigniq.import_validation import MonthlyInputValidation
    from campaigniq.closing_inventory_reconciliation import ClosingInventoryReconciliation
    from types import SimpleNamespace
    contract=monthly_import_contract(2026,9)
    preflight=MonthlyImportPreflight(contract,tuple(MonthlyInputValidation(r.role,True,'test') for r in contract.requirements),None,LotBook())
    inputs={}
    for r in contract.user_supplied_requirements:
        path=tmp_path/(r.role.value+'.txt');path.write_text('synthetic evidence\n');inputs[r.role]=path
    from campaigniq.domain.position_history import PositionHistory
    result=SimpleNamespace(opening_lot_book=LotBook(), position_history=PositionHistory(), campaigns=(), ending_lot_book=LotBook(),forex_settlement_attributions=(),lifecycle_transitions=(),crypto_report=None,forex_transaction_report=None,boundary_reconstruction=SimpleNamespace(unresolved_positions=(),unresolved_campaigns=(),historical_requirements=()))
    monkeypatch.setattr(module,'read_position_snapshot_section',lambda *args,**kwargs:())
    monkeypatch.setattr(module,'read_pending_position_activity',lambda *args,**kwargs:())
    monkeypatch.setattr(module,'reconcile_closing_inventory',lambda **kwargs:ClosingInventoryReconciliation(()))
    monkeypatch.setattr(module,'attribute_period_realized_pnl',lambda result:())
    seen={}
    def run(**kwargs):seen.update(kwargs);return result
    monkeypatch.setattr(module,'PeriodImportPipeline',lambda:SimpleNamespace(run=run))
    storage=LocalFilesystemArtifactStorage(tmp_path/'state')
    outcome=module.execute_monthly_import(preflight,authoritative_state_root=storage.root,supplied_inputs=inputs,artifact_storage=storage)
    assert outcome.finalized and seen['campaign_namespace']=='2026-09'
