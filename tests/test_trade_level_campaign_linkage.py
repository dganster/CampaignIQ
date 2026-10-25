"""Trade-level FIFO links must preserve independent carried campaigns."""
from dataclasses import replace, asdict
from datetime import date, datetime
from decimal import Decimal
from importlib.util import spec_from_file_location, module_from_spec
import json
from pathlib import Path
import pytest
from campaigniq.domain.campaign import Campaign
from campaigniq.domain.campaign_continuity import plan_campaign_continuity, CampaignContinuityError
from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_effect import PositionEffect as Effect
from campaigniq.domain.position_event import PositionChange,PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.import_pipeline import PeriodImportPipeline
from campaigniq.persistence.lot_book_store import serialize_lot_book

CALL=OptionContract('AMD',date(2025,3,21),Decimal(210),OptionType.CALL)
NEXT=replace(CALL,strike=Decimal(120))
PUT=OptionContract('AMD',date(2025,3,21),Decimal(125),OptionType.PUT)
STOCK=Instrument('AMD')
OLD_CALL='2025-01:AMD:CAMP-000016';OLD_PUT='2025-01:AMD:CAMP-000017';NEW='2025-02:AMD:CAMP-000008'


def leg(instrument,side,effect,qty,day=7,second=0):
    ex=Execution(Decimal(qty),Decimal('1'),datetime(2025,2,day,8,15,second))
    if isinstance(instrument,OptionContract):return OptionLeg(instrument,side,effect,(ex,),broker_strategy='SINGLE')
    return InstrumentLeg(instrument,side,effect,(ex,))


def opening(stock=False):
    b=LotBook()
    for lid,inst,cid in [('call-original',CALL,OLD_CALL),('put-original',PUT,OLD_PUT)]:
        b.seed(Lot(lid,inst,Decimal(-5),datetime(2025,1,1),Decimal(100),'BROKER',cid))
    if stock:b.seed(Lot('unknown-shares',STOCK,Decimal(500),datetime(2024,12,31),None))
    return b


def roll():return Trade((leg(NEXT,Side.SELL,Effect.OPEN,'5'),leg(CALL,Side.BUY,Effect.CLOSE,'5')))
def close_put():return Trade((leg(PUT,Side.BUY,Effect.CLOSE,'5',second=1),))


def plan(book,campaigns,events=()):
    history=PositionHistory()
    for c in campaigns:
        for t in c.trades:history.add_trade(t)
    for e in events:history.add_event(e)
    before=serialize_lot_book(period_end=date(2025,1,31),lot_book=book)
    p=plan_campaign_continuity(opening_lot_book=book,position_history=history,campaigns=campaigns)
    assert before==serialize_lot_book(period_end=date(2025,1,31),lot_book=book)
    return p


def test_amd_roll_and_put_close_in_one_monthly_group_keep_distinct_carried_ids():
    b=opening();a,c=roll(),close_put();source=Campaign(NEW,(a,c),True)
    p=plan(b,(source,))
    assert not p.blocking_findings
    assert p.bindings==((NEW,OLD_CALL),(NEW,OLD_PUT))
    assert {x.campaign_id:x.trades for x in p.campaigns}=={OLD_CALL:(a,),OLD_PUT:(c,)}
    assert source.campaign_id==NEW and source.trades==(a,c)
    assert [x.supported_by_fifo for x in p.trade_bindings]==[True,True]
    assert p.trade_bindings[0].consumed_lot_quantities==( ('call-original','5'), )


def test_one_atomic_trade_cannot_merge_two_carried_campaigns():
    t=Trade((leg(CALL,Side.BUY,Effect.CLOSE,'5'),leg(PUT,Side.BUY,Effect.CLOSE,'5')))
    p=plan(opening(),(Campaign(NEW,(t,)),))
    assert any('distinct carried campaigns' in f for f in p.blocking_findings)
    assert not p.trade_bindings[0].supported_by_fifo
    with pytest.raises(ValueError):_=p.campaigns


def test_unknown_stock_consumption_does_not_invalidate_an_earlier_proven_roll():
    a=roll();close=Trade((leg(NEXT,Side.BUY,Effect.CLOSE,'5',day=8),leg(STOCK,Side.SELL,Effect.CLOSE,'500',day=8)))
    p=plan(opening(stock=True),(Campaign(NEW,(a,close),True),))
    assert any('unknown campaign ancestry' in f for f in p.blocking_findings)
    assert p.trade_bindings[0].supported_by_fifo and p.trade_bindings[0].target_campaign_id==OLD_CALL
    assert not p.trade_bindings[1].supported_by_fifo
    with pytest.raises(ValueError):_=p.campaigns
    error=CampaignContinuityError(p)
    assert error.trade_bindings==p.trade_bindings and 'blocked' in str(error)


def test_new_activity_after_flat_does_not_inherit_by_monthly_group_name():
    b=opening();close=close_put();new=Trade((leg(PUT,Side.SELL,Effect.OPEN,'2',day=8),))
    p=plan(b,(Campaign(NEW,(close,new)),))
    assert not p.blocking_findings
    assert [(c.campaign_id,c.trades) for c in p.campaigns]==[(OLD_PUT,(close,)),(NEW,(new,))]
    assert p.trade_bindings[1].evidence_kind=='new_source_campaign'


def test_unanchored_partial_fills_keep_source_grouping():
    a=Trade((leg(PUT,Side.SELL,Effect.OPEN,'2'),));b=Trade((leg(PUT,Side.SELL,Effect.OPEN,'3',second=1),))
    p=plan(LotBook(),(Campaign(NEW,(a,b)),))
    assert not p.blocking_findings and not p.bindings
    assert len(p.campaigns)==1 and p.campaigns[0].trades==(a,b)
    assert all(x.target_campaign_id==NEW for x in p.trade_bindings)


def test_opening_add_then_fifo_close_can_link_to_one_old_campaign():
    b=opening();a=Trade((leg(CALL,Side.SELL,Effect.OPEN,'3'),));c=Trade((leg(CALL,Side.BUY,Effect.CLOSE,'8',day=8),))
    p=plan(b,(Campaign(NEW,(a,c)),))
    assert not p.blocking_findings and p.bindings==((NEW,OLD_CALL),)
    assert len(p.campaigns)==1 and p.campaigns[0].campaign_id==OLD_CALL


def test_atomic_assignment_cannot_combine_distinct_option_ancestries():
    b=opening();b.seed(Lot('second-call',CALL,Decimal(-5),datetime(2025,1,1),None,campaign_id='OTHER'))
    event=PositionEvent(PositionEventKind.ASSIGNMENT,(PositionChange(CALL,Decimal(10)),PositionChange(STOCK,Decimal(-1000))),datetime(2025,2,10))
    p=plan(b,(),(event,))
    assert any('distinct carried campaigns' in f for f in p.blocking_findings)


def test_assignment_from_partial_lots_of_one_campaign_stays_one_campaign():
    b=opening();b.seed(Lot('second-call',CALL,Decimal(-5),datetime(2025,1,1),None,campaign_id=OLD_CALL))
    event=PositionEvent(PositionEventKind.ASSIGNMENT,(PositionChange(CALL,Decimal(10)),PositionChange(STOCK,Decimal(1000))),datetime(2025,2,8))
    close=Trade((leg(STOCK,Side.SELL,Effect.CLOSE,'1000',day=9),))
    p=plan(b,(Campaign(NEW,(close,),True),),(event,))
    assert not p.blocking_findings and p.campaigns[0].campaign_id==OLD_CALL


def test_failed_overclose_is_not_reported_as_supported():
    bad=Trade((leg(PUT,Side.BUY,Effect.CLOSE,'6'),))
    p=plan(opening(),(Campaign(NEW,(bad,),True),))
    assert p.blocking_findings and not p.trade_bindings[0].supported_by_fifo


def test_repeated_trade_is_not_a_second_supported_link():
    t=close_put();h=PositionHistory();h.add_trade(t);h.add_trade(t)
    p=plan_campaign_continuity(opening_lot_book=opening(),position_history=h,campaigns=(Campaign(NEW,(t,)),))
    assert p.blocking_findings and len(p.trade_bindings)==1
    assert not p.trade_bindings[0].supported_by_fifo


def test_duplicate_source_ownership_has_no_supported_target():
    t=close_put();h=PositionHistory();h.add_trade(t)
    p=plan_campaign_continuity(opening_lot_book=opening(),position_history=h,campaigns=(Campaign(NEW,(t,)),Campaign('OTHER-NEW',(t,))))
    assert p.blocking_findings and not p.trade_bindings[0].supported_by_fifo


def test_internal_temporary_ids_cannot_collide_with_carried_identifiers():
    b=opening();actual='__CONTINUITY_TRADE_000001__'
    b.reassign_campaign('put-original',actual)
    p=plan(b,(Campaign(NEW,(close_put(),)),))
    assert not p.blocking_findings and p.campaigns[0].campaign_id==actual
    assert p.trade_bindings[0].target_campaign_id==actual


def test_links_are_deterministic_and_json_serializable():
    source=Campaign(NEW,(roll(),close_put()),True)
    a=plan(opening(),(source,));b=plan(opening(),(source,))
    assert a.trade_bindings==b.trade_bindings and a.campaigns==b.campaigns
    payload=json.loads(json.dumps([asdict(x) for x in a.trade_bindings]))
    assert [x['target_campaign_id'] for x in payload]==[OLD_CALL,OLD_PUT]


def history_file(tmp_path):
    p=tmp_path/'amd.csv'
    p.write_text('Account Statement synthetic\n\nAccount Trade History\n'
        'Notes,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type\n'
        ',2/7/25 08:15:06,VERTICAL,SELL,-5,TO OPEN,AMD,21 MAR 25,120,CALL,1.99,1.96,LMT\n'
        ',,,BUY,+5,TO CLOSE,AMD,21 MAR 25,210,CALL,.03,CREDIT,\n'
        ',2/7/25 08:18:26,SINGLE,BUY,+5,TO CLOSE,AMD,21 MAR 25,125,PUT,18.45,18.45,LMT\n\n'
        'Forex Statements\nTrade Date,Exec Date,Exec Time,Type,Ref #,Description,Misc Fees,Fees,Amount,Balance\n\n'
        'Cash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions,Amount,Balance\n\n')
    return p


def run(book,path,preserve=True):
    return PeriodImportPipeline().run(period_start=date(2025,2,1),period_end=date(2025,2,28),thinkorswim_trade_history=path,
        carried_opening_lot_book=book,campaign_namespace='2025-02',preserve_carried_state=preserve)


def test_real_amd_parser_and_pipeline_preserve_separate_covered_call_and_put(tmp_path):
    b=opening();before=serialize_lot_book(period_end=date(2025,1,31),lot_book=b)
    result=run(b,history_file(tmp_path))
    assert {c.campaign_id for c in result.campaigns}=={OLD_CALL,OLD_PUT}
    assert not result.ending_lot_book.lots(PUT)
    assert result.ending_lot_book.lots(NEXT)[0].campaign_id==OLD_CALL
    assert serialize_lot_book(period_end=date(2025,1,31),lot_book=result.opening_lot_book)==before
    assert serialize_lot_book(period_end=date(2025,1,31),lot_book=b)==before
    assert all(link.supported_by_fifo for link in result.campaign_identity_links)


def test_default_pipeline_does_not_enable_new_linkage(tmp_path):
    result=run(opening(),history_file(tmp_path),False)
    assert result.campaign_identity_links==()
    assert sum(l.quantity for l in result.ending_lot_book.lots(NEXT))==Decimal(-5)


def test_blocked_preflight_keeps_partial_link_evidence_without_authorizing(monkeypatch):
    from campaigniq import historical_backfill_preflight as audit
    spec=spec_from_file_location('whole_year_helpers',Path(__file__).with_name('test_whole_year_backfill_preflight.py'))
    h=module_from_spec(spec);spec.loader.exec_module(h)
    from campaigniq.import_contract import monthly_import_contract
    from campaigniq.import_preflight import MonthlyImportPreflight
    from campaigniq.import_validation import MonthlyInputValidation
    from campaigniq.monthly_import_execution import MonthlyImportExecution
    from campaigniq.closing_inventory_reconciliation import ClosingInventoryReconciliation
    blocked=plan(opening(stock=True),(Campaign(NEW,(Trade((leg(STOCK,Side.SELL,Effect.CLOSE,'500'),)),),True),))
    def prepare(year,month,**kwargs):
        contract=monthly_import_contract(year,month)
        return MonthlyImportPreflight(contract,tuple(MonthlyInputValidation(r.role,True,'fixture') for r in contract.requirements),None,LotBook())
    def execute(preflight,**kwargs):
        if kwargs.get('development_continuity') and preflight.contract.period_start.month==2:raise CampaignContinuityError(blocked)
        return MonthlyImportExecution(h.result(),ClosingInventoryReconciliation(()),None)
    monkeypatch.setattr(audit,'prepare_monthly_import',prepare);monkeypatch.setattr(audit,'execute_monthly_import',execute)
    report=audit.run_preflight(h.bundle(range(1,13)),campaign_continuity=True)
    feb=report['months'][1]['campaign_continuity']
    assert feb['trade_links'] and not feb['trade_links'][0]['supported_by_fifo']
    assert feb['mode']=='legacy_quantity_fallback' and not report['identity_preserving_chain_verified']
    assert report['diagnostic_position_replay_completed'] and not report['publication_authorized']
