"""Carried identity is inherited through FIFO evidence, never source mutation."""
from datetime import date, datetime
from decimal import Decimal
from dataclasses import replace
import pytest
from campaigniq.domain.campaign import Campaign
from campaigniq.domain.campaign_continuity import plan_campaign_continuity
from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.import_pipeline import PeriodImportPipeline
from campaigniq.persistence.lot_book_store import serialize_lot_book

OLD='2025-07:TEST:CAMP-000001'
NEW='2025-08:TEST:CAMP-000002'
STOCK=Instrument('TEST')
OPT=OptionContract('TEST',date(2025,9,19),Decimal('100'),OptionType.CALL)


def book(instrument=STOCK,quantity='100',cid=OLD):
    b=LotBook();b.seed(Lot('original',instrument,Decimal(quantity),datetime(2025,7,1),
        Decimal('1000'),basis_source='BROKER',campaign_id=cid));return b


def trade(instrument=STOCK,side=Side.SELL,effect=PositionEffect.CLOSE,qty='40',day=7):
    execution=Execution(Decimal(qty),Decimal('10'),datetime(2025,8,day,10))
    leg=(OptionLeg(instrument,side,effect,(execution,),broker_strategy='SINGLE')
        if isinstance(instrument,OptionContract) else InstrumentLeg(instrument,side,effect,(execution,)))
    return Trade(legs=(leg,))


def campaign(trades,cid=NEW):
    return Campaign(cid,tuple(trades),started_before_data=True)


def plan(b,campaigns,events=()):
    h=PositionHistory()
    for c in campaigns:
        for t in c.trades:h.add_trade(t)
    for event in events:h.add_event(event)
    before=serialize_lot_book(period_end=date(2025,7,31),lot_book=b)
    p=plan_campaign_continuity(opening_lot_book=b,position_history=h,campaigns=campaigns)
    assert serialize_lot_book(period_end=date(2025,7,31),lot_book=b)==before
    return p


def test_partial_fifo_close_inherits_carried_identity_without_splitting_source():
    b=book();c=campaign([trade()]);p=plan(b,[c])
    assert not p.blocking_findings
    assert p.bindings==((NEW,OLD),)
    assert p.campaigns[0].campaign_id==OLD
    assert c.campaign_id==NEW
    assert b.lots(STOCK)[0].quantity==Decimal('100')


def test_isrg_shape_new_three_contracts_before_close_of_five_inherits_old_two():
    b=book(OPT,'-2')
    c=campaign([trade(OPT,Side.SELL,PositionEffect.OPEN,'3',1),
                trade(OPT,Side.BUY,PositionEffect.CLOSE,'5',7)])
    p=plan(b,[c])
    assert not p.blocking_findings
    assert p.bindings==((NEW,OLD),)


def test_new_campaign_after_flat_does_not_inherit_prior_id_by_symbol():
    c1=campaign([trade(qty='100')]);c2=campaign([trade(side=Side.BUY,effect=PositionEffect.OPEN,day=8)],cid='NEW-SECOND')
    p=plan(book(),[c1,c2])
    assert not p.blocking_findings
    assert [c.campaign_id for c in p.campaigns]==[OLD,'NEW-SECOND']


def test_distinct_carried_ids_cannot_be_merged():
    b=book();b.seed(replace(b.lots(STOCK)[0],lot_id='second',campaign_id='DIFFERENT'))
    p=plan(b,[campaign([trade(qty='200')])])
    assert any('distinct carried campaigns' in f for f in p.blocking_findings)


def test_fifo_selects_first_carried_lot_without_arbitrary_subset():
    b=book();b.seed(replace(b.lots(STOCK)[0],lot_id='second',campaign_id='DIFFERENT'))
    p=plan(b,[campaign([trade(qty='50')])])
    assert not p.blocking_findings
    assert p.campaigns[0].campaign_id==OLD


def test_unknown_campaign_ancestry_remains_blocked():
    p=plan(book(cid=None),[campaign([trade()])])
    assert any('unknown campaign ancestry' in f for f in p.blocking_findings)


def test_assignment_event_propagates_existing_option_identity_to_new_shares():
    b=book(OPT,'-1')
    event=PositionEvent(PositionEventKind.ASSIGNMENT,
        (PositionChange(OPT,Decimal('1')),PositionChange(STOCK,Decimal('100'))),datetime(2025,8,1))
    p=plan(b,[campaign([trade(qty='100')])],[event])
    assert not p.blocking_findings
    assert p.campaigns[0].campaign_id==OLD


def test_expiration_consuming_unknown_provenance_stays_blocked():
    b=book(OPT,'-1',None)
    event=PositionEvent(PositionEventKind.EXPIRATION,(PositionChange(OPT,Decimal('1')),),datetime(2025,8,1))
    assert plan(b,[],[event]).blocking_findings


def test_cross_underlying_carried_collision_is_blocked():
    b=book();b.seed(Lot('other',Instrument('OTHER'),Decimal('1'),datetime(2025,7,1),None,campaign_id=OLD))
    assert any('multiple underlyings' in f for f in plan(b,[]).blocking_findings)


def test_new_id_cannot_collide_with_carried_id():
    assert plan(book(),[campaign([trade()],cid=OLD)]).blocking_findings


def test_unowned_or_missing_history_trade_is_blocked():
    h=PositionHistory();h.add_trade(trade())
    p=plan_campaign_continuity(opening_lot_book=book(),position_history=h,campaigns=())
    assert p.blocking_findings
    p=plan_campaign_continuity(opening_lot_book=book(),position_history=PositionHistory(),campaigns=(campaign([trade()]),))
    assert p.blocking_findings


def test_repeated_history_trade_cannot_consume_twice():
    t=trade();h=PositionHistory();h.add_trade(t);h.add_trade(t)
    p=plan_campaign_continuity(opening_lot_book=book(),position_history=h,campaigns=(campaign([t]),))
    assert p.blocking_findings


def test_new_closed_trade_stays_new_without_prior_lot_link():
    c=campaign([trade(side=Side.BUY,effect=PositionEffect.OPEN,day=1),trade(day=2)])
    p=plan(LotBook(),[c])
    assert not p.blocking_findings and not p.bindings
    assert p.campaigns[0].campaign_id==NEW


def history_file(tmp_path):
    p=tmp_path/'history.csv'
    p.write_text('Account Statement synthetic\n\nAccount Trade History\n'
        ',Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type\n'
        ',8/7/25 10:00:00,STOCK,SELL,-40,TO CLOSE,TEST,,,STOCK,10.00,10.00,MKT\n\n'
        'Forex Statements\nTrade Date,Exec Date,Exec Time,Type,Ref #,Description,Misc Fees,Fees,Amount,Balance\n\n'
        'Cash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions,Amount,Balance\n\n')
    return p


def run(b,path,**kwargs):
    args=dict(period_start=date(2025,8,1),period_end=date(2025,8,31),
        thinkorswim_trade_history=path,carried_opening_lot_book=b,campaign_namespace='2025-08',preserve_carried_state=True)
    args.update(kwargs)
    return PeriodImportPipeline().run(**args)


def test_real_pipeline_mode_preserves_exact_opening_and_ending_campaign(tmp_path):
    b=book();before=serialize_lot_book(period_end=date(2025,7,31),lot_book=b)
    result=run(b,history_file(tmp_path))
    assert serialize_lot_book(period_end=date(2025,7,31),lot_book=result.opening_lot_book)==before
    lot,=result.ending_lot_book.lots(STOCK)
    assert lot.campaign_id==OLD and lot.quantity==Decimal('60')
    assert result.campaigns[0].campaign_id==OLD
    # Legacy exact-boundary evidence checks remain visible for a partial close.
    assert result.boundary_reconstruction.unresolved_campaigns == (OLD,)
    assert serialize_lot_book(period_end=date(2025,7,31),lot_book=b)==before


def test_real_pipeline_mode_does_not_guess_unknown_identity(tmp_path):
    with pytest.raises(ValueError,match='unknown campaign ancestry'):
        run(book(cid=None),history_file(tmp_path))


@pytest.mark.parametrize('kwargs', [
    {'historical_trade_histories':('not-read.csv',)},
    {'historical_source_root':'not-read'}, {'historical_period_start':date(2025,7,1)},
    {'opening_snapshot':'not-read.txt'}, {'campaign_namespace':'WRONG'},
    {'period_start':date(2025,8,2)}, {'period_end':date(2025,8,30)},
    {'preserve_carried_state':1},
])
def test_continuity_mode_rejects_unsafe_sources_or_scope(kwargs):
    with pytest.raises(ValueError):
        run(book(),'must-not-be-read.csv',**kwargs)


def test_default_pipeline_behavior_remains_available(tmp_path):
    result=run(book(),history_file(tmp_path),preserve_carried_state=False)
    assert result.ending_lot_book.lots(STOCK)[0].quantity==Decimal('60')


def test_event_cannot_create_phantom_option_inventory():
    event=PositionEvent(PositionEventKind.EXPIRATION,(PositionChange(OPT,Decimal('1')),),datetime(2025,8,1))
    assert any('sufficient carried/opened inventory' in f for f in plan(LotBook(),[],[event]).blocking_findings)


def test_fifo_overclose_stays_blocked():
    assert plan(book(),[campaign([trade(qty='101')])]).blocking_findings


def test_separate_monthly_closures_can_share_exactly_one_carried_identity():
    p=plan(book(),[campaign([trade(qty='40')]),
        campaign([trade(qty='60',day=8)],cid='MONTH-SECOND')])
    assert not p.blocking_findings
    assert {c.campaign_id for c in p.campaigns}=={OLD}


def test_unconsumed_unknown_lot_is_preserved_without_guessing_ancestry():
    b=book();b.seed(Lot('unrelated',Instrument('OTHER'),Decimal('1'),datetime(2025,7,1),None))
    assert not plan(b,[campaign([trade()])]).blocking_findings
    assert b.lots(Instrument('OTHER'))[0].campaign_id is None


def test_continuity_forex_uses_carried_position_without_historical_reseeding(tmp_path):
    from campaigniq.domain.value_objects.forex_pair import ForexPair
    pair=ForexPair.from_symbol('USD/CAD')
    b=book(pair,'50000',cid='2025-07:USD%2FCAD:FX-CAMP-000001')
    path=history_file(tmp_path)
    text=path.read_text().replace(',8/7/25 10:00:00,STOCK,SELL,-40,TO CLOSE,TEST,,,STOCK,10.00,10.00,MKT\n','')
    text=text.replace('Type,Ref #,Description,Misc Fees,Fees,Amount,Balance\n',
        'Type,Ref #,Description,Misc Fees,Fees,Amount,Balance\n'
        ',8/7/25,05:02:50,TRD,="12345","SOLD -50,000 USD/CAD @1.37265",--,--,--,0\n')
    path.write_text(text)
    result=run(b,path)
    assert not result.ending_lot_book.lots(pair)
    assert result.campaigns[0].campaign_id=='2025-07:USD%2FCAD:FX-CAMP-000001'


def test_blocked_plan_cannot_supply_campaigns_for_replay():
    p=plan(book(cid=None),[campaign([trade()])])
    with pytest.raises(ValueError,match='blocked continuity plan'):
        _=p.campaigns


def test_exact_full_closure_retains_existing_boundary_verification(tmp_path):
    path=history_file(tmp_path)
    path.write_text(path.read_text().replace('SELL,-40,','SELL,-100,'))
    result=run(book(),path)
    assert not result.boundary_reconstruction.unresolved_campaigns
    assert not result.ending_lot_book.lots(STOCK)
    assert result.opening_lot_book.lots(STOCK)[0].campaign_id==OLD


def test_isrg_shape_realized_allocations_retain_one_carried_campaign():
    from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
    from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
    b=book(OPT,'-2')
    c=campaign([trade(OPT,Side.SELL,PositionEffect.OPEN,'3',1),
                trade(OPT,Side.BUY,PositionEffect.CLOSE,'5',7)])
    p=plan(b,[c])
    record=RealizedGainLossRecord(date(2025,8,7),OPT,Decimal('5'),Decimal('10'),
        Decimal('6000'),Decimal('5000'),Decimal('1000'),'FIFO','SHORT')
    attribution,=RealizedLotAttributor(b.clone()).attribute_campaigns(list(p.campaigns),[record])
    assert attribution.campaign_ids == (OLD,)
    assert {a.campaign_id for a in attribution.allocations}=={OLD}
    assert attribution.basis_reconciled and attribution.gain_loss_reconciled


def test_legacy_carried_id_is_not_rescoped(tmp_path):
    result=run(book(cid='CAMP-000001'),history_file(tmp_path))
    assert result.opening_lot_book.lots(STOCK)[0].campaign_id=='CAMP-000001'
    assert result.ending_lot_book.lots(STOCK)[0].campaign_id=='CAMP-000001'
