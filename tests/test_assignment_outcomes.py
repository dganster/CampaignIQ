from copy import deepcopy
from datetime import date
from decimal import Decimal as D
import pytest
from campaigniq.analytics.assignment_outcomes import assignment_outcomes
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument
CID='2026-06:APD:CAMP-1'
def event(kind='PUT',shares='100',cid=CID):
    common=dict(action='Assignment',occurred_at='2026-06-19T00:00:00',period_start='2026-06-01',campaign_id=cid,source='Published position journal')
    return (dict(common,instrument=dict(type='option',underlying='APD',expiration='2026-06-19',strike='270',option_type=kind),quantity_change='1',lots=[dict(lot_id='option',quantity='1')]),dict(common,instrument=dict(type='instrument',symbol='APD'),quantity_change=shares,position_after='100' if D(shares)>0 else '0',lots=[dict(lot_id='stock',quantity='100')]))
def close(stock,qty='100',month='2026-06',day='2026-06-25T09:00:00'):
    return dict(stock,action='Close / reduce',occurred_at=day,period_start=month+'-01',quantity_change='-'+qty,lots=[dict(lot_id='stock',quantity=qty)])
def broker(day=date(2026,6,25),qty='100',cid=CID,lot='stock',pnl='50'):
    record=RealizedGainLossRecord(day,Instrument('APD'),D(qty),D('1'),D('1000')+D(pnl),D('1000'),D(pnl),'FIFO','ST',D('0'))
    return RealizedAttribution(record,(LotAllocation(lot,D(qty),D('1000'),campaign_id=cid),))
def test_open_put():
    o,s=event();r=assignment_outcomes([o,s],[o])[0]
    assert r['remaining']==100 and r['strike_cash']==-27000 and r['realized'] is None

def test_call_boundary_broker_result():
    o,s=event('CALL','-100')
    for r in (o,s):r.update(occurred_at='2026-07-01T00:00:00',period_start='2026-07-01')
    r=assignment_outcomes([o,s],[o],[broker(date(2026,6,30),pnl='-975.27')])[0]
    assert r['strike_cash']==27000 and r['realized']==D('-975.27') and r['remaining'] is None

@pytest.mark.parametrize('qty,remaining',[('40',60),('100',0)])
def test_subsequent_closes(qty,remaining):
    o,s=event();r=assignment_outcomes([o,s,close(s,qty)],[o],[broker(qty=qty),broker(lot='other',pnl='900')])[0]
    assert r['remaining']==remaining and r['realized']==50 and r['broker_records']==1

@pytest.mark.parametrize('change',['time','direction','multiple','lots'])
def test_ambiguous_delivery(change):
    o,s=event();entries=[o,s]
    if change=='time':s['occurred_at']='2026-06-19T00:00:01'
    if change=='direction':s['quantity_change']='-100'
    if change=='multiple':entries.append(deepcopy(o))
    if change=='lots':s['lots'][0]['quantity']='99'
    r=assignment_outcomes(entries,[o])[0];assert not r['linked'] and r['remaining'] is None

@pytest.mark.parametrize('change',['exercise','source','future','long','unselected'])
def test_excluded(change):
    o,s=event();selected=[o]
    if change=='exercise':o['action']='Exercise'
    if change=='source':o['source']='Retained trades; campaign link unavailable'
    if change=='future':o['instrument']['underlying']='/ES'
    if change=='long':o['quantity_change']='-1'
    if change=='unselected':selected=[]
    assert not assignment_outcomes([o,s],selected)

def test_cross_month_requires_original_opening_reference():
    o,s=event();sold=close(s,month='2026-07',day='2026-07-10T09:00:00')
    assert assignment_outcomes([o,s,sold],[o])[0]['remaining'] is None
    carry=dict(s,action='Carried position',period_start='2026-07-01',occurred_at='2026-07-01T00:00:00',opening_refs=[dict(lot_id='stock',opened_at=o['occurred_at'],quantity='100')])
    r=assignment_outcomes([o,s,carry,sold],[o],[broker(date(2026,7,10))])[0];assert r['remaining']==0 and r['realized']==50
    carry['opening_refs'][0]['opened_at']='2026-06-01T00:00:00'
    assert assignment_outcomes([o,s,carry,sold],[o])[0]['remaining'] is None

def test_legacy_ids_not_linked_across_months():
    o,s=event(cid='CAMP-1');sold=close(s,month='2026-07',day='2026-07-10T09:00:00')
    assert assignment_outcomes([o,s,sold],[o])[0]['remaining'] is None

@pytest.mark.parametrize('change',['campaign','date','quantity','shared'])
def test_broker_mismatch(change):
    o,s=event();b=broker()
    if change=='campaign':b=broker(cid='other')
    if change=='date':b=broker(day=date(2026,6,26))
    if change=='quantity':b=broker(qty='50')
    if change=='shared':b=RealizedAttribution(b.record,(LotAllocation('stock',D('50'),D('500'),campaign_id=CID),LotAllocation('other',D('50'),D('500'),campaign_id=CID)))
    assert assignment_outcomes([o,s,close(s)],[o],[b])[0]['realized'] is None

def test_overlapping_closes_are_not_claimed():
    o,s=event();sold=close(s);r=assignment_outcomes([o,s,sold,dict(sold,occurred_at='2026-06-26T09:00:00')],[o])[0]
    assert not r['linked'] and r['remaining'] is None

def test_no_mutation():
    o,s=event();entries=[o,s];before=deepcopy(entries);assignment_outcomes(entries,[o]);assert entries==before

@pytest.mark.parametrize('kind,shares,after',[('PUT','100','0'),('CALL','-100','-100')])
def test_assignment_covering_short_stock_or_creating_short_stock_needs_review(kind,shares,after):
    o,s=event(kind,shares);s['position_after']=after
    r=assignment_outcomes([o,s],[o])[0];assert not r['linked'] and r['remaining'] is None


def test_view_preserves_integer_counts_and_unavailable_results():
    from campaigniq.ui.assignment_outcomes_view import render_assignment_outcomes
    class UI:
        def markdown(self,*args):pass
        def caption(self,*args):pass
        def dataframe(self,frame,**kwargs):self.frame=frame.data
    ui=UI();o,s=event();render_assignment_outcomes(ui,assignment_outcomes([o,s],[o]))
    assert ui.frame['Contracts'].iloc[0]==1
    assert ui.frame['Share change'].iloc[0]==100
    assert ui.frame['Cash at strike'].iloc[0]==-27000
    assert ui.frame['Linked stock realized P&L'].iloc[0] is None

@pytest.mark.parametrize('kind',[ 'PUT', 'CALL'])
def test_outcomes_from_actual_assignment_journal_replay(kind):
    from datetime import datetime
    from types import SimpleNamespace
    from campaigniq.domain.lot import Lot
    from campaigniq.domain.lot_book import LotBook
    from campaigniq.domain.option_contract import OptionContract
    from campaigniq.domain.option_type import OptionType
    from campaigniq.domain.position_event import PositionEvent, PositionChange
    from campaigniq.domain.position_event_kind import PositionEventKind
    from campaigniq.domain.position_history import PositionHistory
    from campaigniq.domain.lot_book_period_applier import LotBookPeriodApplier
    from campaigniq.persistence.position_journal import capture_position_journal
    book=LotBook();contract=OptionContract('APD',date(2026,6,19),D('270'),OptionType[kind])
    book.seed(Lot('option',contract,D('-1'),datetime(2026,5,1),None,campaign_id=CID))
    if kind=='CALL':book.seed(Lot('stock',Instrument('APD'),D('100'),datetime(2026,5,1),None,campaign_id=CID))
    shares=D('100' if kind=='PUT' else '-100');history=PositionHistory()
    history.add_event(PositionEvent(PositionEventKind.ASSIGNMENT,(PositionChange(contract,D('1')),PositionChange(Instrument('APD'),shares)),datetime(2026,6,19)))
    ending=LotBookPeriodApplier().apply(opening_lot_book=book,position_history=history,campaigns=())
    result=SimpleNamespace(opening_lot_book=book,ending_lot_book=ending,position_history=history,campaigns=())
    rows=[dict(r,source='Published position journal',period_start='2026-06-01') for r in capture_position_journal(result,date(2026,6,1))]
    selected=[r for r in rows if r['action']=='Assignment' and r['instrument']['type']=='option']
    outcome=assignment_outcomes(rows,selected)[0]
    assert outcome['linked'] and outcome['shares']==shares
    assert outcome['remaining']==(100 if kind=='PUT' else None)

def test_assignment_reachable_from_delivered_stock_campaign():
    o,s=event('CALL','-100');o['campaign_id']='2026-06:APD:CAMP-2'
    result=assignment_outcomes([o,s],[s]);assert len(result)==1 and result[0]['linked']
