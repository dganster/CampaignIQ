from copy import deepcopy
from datetime import date,datetime
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from campaigniq.domain.trade import Trade
from campaigniq.domain.side import Side
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.persistence.lot_book_store import _serialize_instrument
from campaigniq.analytics.strategy_comparison import classify_holdings,ObservedStrategy,compare_strategies
from campaigniq.ui.strategy_evidence import load_observed_strategies
import campaigniq.ui.strategy_evidence as source
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.analytics.profit_concentration import ConcentrationCampaign

AT=datetime(2026,5,1,10)
CID='2026-05:APD:CAMP-1'
def leg(kind='CALL',strike='270',side=Side.SELL,qty='1',expiry=date(2026,6,19),effect=PositionEffect.OPEN):
    instrument=OptionContract('APD',expiry,D(strike),OptionType[kind]) if kind!='STOCK' else Instrument('APD')
    return Leg(instrument,side,effect,(Execution(D(qty),D('3'),AT),))

def rows(trade):
    return [dict(source='Published position journal',period_start='2026-05-01',campaign_id=CID,occurred_at=AT.isoformat(),
                 instrument=_serialize_instrument(l.instrument),action='Open / add',quantity_change=str(sum(abs(e.quantity) for e in l.executions)*(1 if l.side is Side.BUY else -1)),price='3',position_after='0',lots=[]) for l in trade.legs]

def campaign(pnl='100',cid=CID):return ConcentrationCampaign(cid,'APD',D(pnl),'2026-05/'+cid)

def reader(monkeypatch,trades,valid=True):
    monkeypatch.setattr(source,'_source_matches_manifest',lambda *a:valid)
    monkeypatch.setattr(source,'ThinkorswimTradeReader',lambda *a:SimpleNamespace(read=lambda *a,**k:trades))
    return SimpleNamespace(exists=lambda *a:True)

END=date(2026,9,30)


def carry(kind,qty,cid=CID,*,at='2026-05-01T00:00:00'):
    instrument=leg(kind).instrument
    return dict(source='Published position journal',period_start='2026-05-01',occurred_at=at,
                campaign_id=cid,instrument=_serialize_instrument(instrument),action='Carried position',
                quantity_change=qty,position_after=qty,price=None,lots=[dict(lot_id=kind,quantity=str(abs(D(qty))))])


def load(evidence,*,original=None,c= None,end=END,storage=None):
    return load_observed_strategies(storage or SimpleNamespace(exists=lambda *a:False),'.',evidence,
                                   (c or campaign(),),end,original or {})

@pytest.mark.parametrize('holdings,expected',[
    ([(leg('CALL').instrument,D('-1'))],'Short call; coverage unverified'),
    ([(leg('PUT').instrument,D('-1'))],'Short put'),
    ([(leg('STOCK').instrument,D('100'))],'Stock position'),
    ([(leg('STOCK').instrument,D('-100'))],'Short stock position'),
    ([(leg('STOCK').instrument,D('200')),(leg('CALL').instrument,D('-1'))],'Covered call'),
    ([(leg('PUT').instrument,D('-1')),(leg('STOCK').instrument,D('100'))],'Unclassified')])
def test_snapshot_shapes_without_manufactured_trades(holdings,expected):assert classify_holdings(holdings)==expected

def test_carried_covered_call_has_observed_basis_and_does_not_prove_entry():
    evidence=[carry('STOCK','100'),carry('CALL','-1')];before=deepcopy(evidence)
    r=load(evidence)[('APD',CID)]
    assert r.strategy=='Covered call' and r.basis=='Carried-position snapshot' and r.observed_at==datetime(2026,5,1)
    assert 'original entry is not established' in r.reason and evidence==before


def test_snapshot_stock_covers_call_in_different_campaign_with_no_double_coverage():
    evidence=[carry('CALL','-1'),carry('STOCK','100',CID+'2')]
    assert load(evidence)[('APD',CID)].strategy=='Covered call'
    evidence.append(carry('CALL','-1',CID+'3'))
    assert load(evidence)[('APD',CID)].strategy=='Short call; coverage unverified'


def test_snapshot_incomplete_lots_remain_unclassified():
    r=carry('PUT','-1');r['lots'][0]['quantity']='0.5'
    assert load([r])[('APD',CID)].strategy=='Unclassified'


def test_snapshots_not_combined_across_timestamps():
    evidence=[carry('CALL','-1'),carry('STOCK','100',at='2026-05-01T00:00:01')]
    assert load(evidence)[('APD',CID)].strategy=='Short call; coverage unverified'


def test_do_not_skip_unsupported_first_observation_for_later_supported_position():
    evidence=[carry('STOCK','100'),carry('PUT','-1')]
    evidence.append(dict(evidence[1],occurred_at='2026-06-01T00:00:00',period_start='2026-06-01'))
    assert load(evidence)[('APD',CID)].strategy=='Unclassified'


def test_future_and_unverified_observations_ignored():
    evidence=[carry('PUT','-1')]
    assert load(evidence,end=date(2026,4,30))[('APD',CID)].observed_at is None
    evidence[0]['source']='Retained trades; campaign link unavailable'
    assert load(evidence)[('APD',CID)].strategy=='Unclassified'


def test_close_only_rows_cannot_establish_position_shape():
    r=carry('PUT','-1');r['action']='Close / reduce'
    assert load([r])[('APD',CID)].strategy=='Unclassified'


def test_legacy_observations_restricted_to_reporting_month():
    c=campaign(cid='2026-05/CAMP-1');may=carry('PUT','-1','CAMP-1')
    april=dict(carry('CALL','-1','CAMP-1'),period_start='2026-04-01',occurred_at='2026-04-01T00:00:00')
    result=load([april,may],c=c)[('APD',c.campaign_id)]
    assert result.strategy=='Short put' and result.observed_at==datetime(2026,5,1)


def test_original_order_remains_original_and_observed_totals_identical():
    c=campaign();t=Trade((leg('PUT'),));original={('APD',CID):(t,False,'Verified original')}
    observed=load(rows(t),original=original)
    original_groups,original_details=compare_strategies((c,),original,{})
    observed_groups,observed_details=compare_strategies((c,),original,{},observed_evidence=observed)
    assert original_groups[0]['realized']==observed_groups[0]['realized']==100
    assert original_details[0]['basis']=='Original entry' and observed_details[0]['basis']=='Verified opening order'


def test_roll_opening_classifies_only_after_complete_original_order_verification(monkeypatch,tmp_path):
    import campaigniq.ui.roll_evidence as rolls
    closed=leg('CALL','260',Side.BUY,effect=PositionEffect.CLOSE)
    opened=leg('CALL','270')
    t=Trade((closed,opened));r=rows(t)
    r[0].update(action='Roll: close',campaign_id=CID+'OLD');r[1]['action']='Roll: open'
    storage=reader(monkeypatch,[t])
    monkeypatch.setattr(rolls,'_source_matches_manifest',lambda *a:True)
    monkeypatch.setattr(rolls,'ThinkorswimTradeReader',lambda *a:SimpleNamespace(read=lambda *a,**k:[t]))
    results=load_observed_strategies(storage,tmp_path,r,(campaign(),),END,{})
    result=results[('APD',CID)]
    assert result.strategy=='Short call; coverage unverified' and result.basis=='Verified roll opening legs'
    assert 'closing legs were also verified' in result.reason
    r[0]['quantity_change']='2'
    assert load_observed_strategies(storage,tmp_path,r,(campaign(),),END,{})[('APD',CID)].strategy=='Unclassified'


def test_ambiguous_roll_orders_remain_unclassified(monkeypatch,tmp_path):
    import campaigniq.ui.roll_evidence as rolls
    t=Trade((leg('CALL','260',Side.BUY,effect=PositionEffect.CLOSE),leg('CALL','270')));r=rows(t)
    r[0].update(action='Roll: close',campaign_id=CID+'OLD');r[1]['action']='Roll: open'
    storage=reader(monkeypatch,[t,t])
    monkeypatch.setattr(rolls,'_source_matches_manifest',lambda *a:True)
    monkeypatch.setattr(rolls,'ThinkorswimTradeReader',lambda *a:SimpleNamespace(read=lambda *a,**k:[t,t]))
    assert load_observed_strategies(storage,tmp_path,r,(campaign(),),END,{})[('APD',CID)].strategy=='Unclassified'


def test_observed_open_order_requires_verified_source(monkeypatch,tmp_path):
    t=Trade((leg('PUT'),));evidence=rows(t);storage=reader(monkeypatch,[t])
    assert load_observed_strategies(storage,tmp_path,evidence,(campaign(),),END,{})[('APD',CID)].strategy=='Short put'
    monkeypatch.setattr(source,'_source_matches_manifest',lambda *a:False)
    assert load_observed_strategies(storage,tmp_path,evidence,(campaign(),),END,{})[('APD',CID)].strategy=='Unclassified'


def test_observed_ui_separates_basis_and_exports_observation_date():
    from campaigniq.analytics.profit_concentration import ProfitConcentration
    from campaigniq.ui.strategy_comparison_view import render_strategy_comparison
    class UI:
        session_state={}
        captured=[]
        def subheader(self,*a):pass
        def caption(self,*a):pass
        def markdown(self,*a):pass
        def radio(self,*a,**k):return 'First verified observed strategy'
        def dataframe(self,table,**kwargs):self.captured.append((table,kwargs))
        def download_button(self,*a,**k):self.download=(a,k)
    c=campaign();scope=ProfitConcentration((c,),D('100'),D('0'),D('100'),0,D('0'))
    observed={('APD',CID):ObservedStrategy('Covered call','Carried-position snapshot',AT,'Verified snapshot')}
    ui=UI();render_strategy_comparison(ui,scope,{}, {},END,observed_entries=observed)
    frame=ui.captured[-1][0].data
    assert frame['Observed strategy'].iloc[0]=='Covered call' and frame['Classification basis'].iloc[0]=='Carried-position snapshot'
    assert frame['Observation date'].iloc[0]==date(2026,5,1)

def test_original_mode_keeps_snapshot_campaign_unclassified():
    c=campaign();observed={('APD',CID):ObservedStrategy('Covered call','Carried-position snapshot',AT,'snapshot')}
    groups,details=compare_strategies((c,),{}, {},observed_evidence=observed)
    assert groups[0]['strategy']=='Covered call'
    original,_=compare_strategies((c,),{}, {})
    assert original[0]['strategy']=='Unclassified'


def test_observed_review_export_contains_basis_date():
    from campaigniq.analytics.strategy_comparison import unclassified_review
    c=campaign();observed={('APD',CID):ObservedStrategy('Unclassified','Unavailable',AT,'Incomplete first snapshot')}
    _,details=compare_strategies((c,),{}, {},observed_evidence=observed)
    _,rows=unclassified_review(details)
    assert rows[0]['Classification basis']=='Unavailable' and rows[0]['Observation date']==AT.isoformat()
