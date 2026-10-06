from datetime import datetime,date
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from campaigniq.analytics.strategy_comparison import classify_entry,compare_strategies
from campaigniq.analytics.profit_concentration import ConcentrationCampaign,ProfitConcentration
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.side import Side
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.lot_book_store import _serialize_instrument,serialize_lot_book
import campaigniq.ui.strategy_evidence as source
AT=datetime(2026,5,1,10)
CID='2026-05:APD:CAMP-1'
def leg(kind='CALL',strike='270',side=Side.SELL,qty='1',expiry=date(2026,6,19),effect=PositionEffect.OPEN):
    instrument=OptionContract('APD',expiry,D(strike),OptionType[kind]) if kind!='STOCK' else Instrument('APD')
    return Leg(instrument,side,effect,(Execution(D(qty),D('3'),AT),))

def rows(trade):
    return [dict(source='Published position journal',period_start='2026-05-01',campaign_id=CID,occurred_at=AT.isoformat(),
                 instrument=_serialize_instrument(l.instrument),action='Open / add',quantity_change=str(sum(abs(e.quantity) for e in l.executions)*(1 if l.side is Side.BUY else -1)),price='3',position_after='0',lots=[]) for l in trade.legs]

def campaign(pnl='100',cid=CID):return ConcentrationCampaign(cid,'APD',D(pnl),'2026-05/'+cid)

@pytest.mark.parametrize('legs,expected',[
    ([leg('PUT')],'Short put'),([leg()], 'Short call; coverage unverified'),
    ([leg(side=Side.BUY)],'Long call'),([leg('PUT',side=Side.BUY)],'Long put'),
    ([leg('STOCK',side=Side.BUY,qty='100')],'Stock entry'),
    ([leg('STOCK',qty='100')],'Short stock entry'),
    ([leg('STOCK',side=Side.BUY,qty='100'),leg()],'Covered call'),
    ([leg(side=Side.BUY),leg(strike='280')],'Call debit vertical'),
    ([leg(),leg(strike='280',side=Side.BUY)],'Call credit vertical'),
    ([leg('PUT','260'),leg('PUT','270',Side.BUY)],'Put debit vertical'),
    ([leg('PUT','260',Side.BUY),leg('PUT','270')],'Put credit vertical'),
    ([leg('PUT','260'),leg('CALL','280')],'Short strangle'),
    ([leg('PUT','270'),leg('CALL','270')],'Short straddle'),
    ([leg('PUT','250',Side.BUY),leg('PUT','260'),leg('CALL','280'),leg('CALL','290',Side.BUY)],'Iron condor')])
def test_supported_entry_shapes(legs,expected):assert classify_entry(Trade(tuple(legs)))==expected

@pytest.mark.parametrize('legs',[
    [leg(),leg(strike='280',side=Side.BUY,qty='2')],
    [leg(),leg(strike='280',side=Side.BUY,expiry=date(2026,7,17))],
    [leg(effect=PositionEffect.CLOSE),leg(strike='280')],
    [leg(qty='0.5')],[leg('STOCK',side=Side.BUY,qty='99'),leg()],
    [leg('PUT','260'),leg('CALL','250')]])
def test_unsupported_shapes_remain_unclassified(legs):assert classify_entry(Trade(tuple(legs)))=='Unclassified'

def test_covered_call_requires_coverage_evidence():
    assert classify_entry(Trade((leg(),)),covered_call=True)=='Covered call'

def test_comparison_closed_win_rate_excludes_open_and_unknown():
    cs=(campaign('100'),campaign('-50',CID+'2'),campaign('0',CID+'3'),campaign('999',CID+'4'))
    evidence={(c.underlying,c.campaign_id):(Trade((leg('PUT'),)),False,'verified') for c in cs}
    status={('APD',CID):False,('APD',CID+'2'):True,('APD',CID+'3'):False}
    groups,details=compare_strategies(cs,evidence,status);g=groups[0]
    assert g['realized']==1049 and g['closed']==2 and g['open']==1 and g['unknown']==1 and g['win_rate']==D('0.5')

def test_missing_entry_kept_unclassified():
    groups,details=compare_strategies((campaign(),),{},{});assert groups[0]['strategy']=='Unclassified' and groups[0]['realized']==100


def reader(monkeypatch,trades,valid=True):
    monkeypatch.setattr(source,'_source_matches_manifest',lambda *a:valid)
    monkeypatch.setattr(source,'ThinkorswimTradeReader',lambda *a:SimpleNamespace(read=lambda *a,**k:trades))
    return SimpleNamespace(exists=lambda *a:True)

def test_verified_entry_and_partial_fill_average(monkeypatch,tmp_path):
    l=leg('PUT');l=Leg(l.instrument,l.side,l.position_effect,(Execution(D('0.5'),D('2'),AT),Execution(D('0.5'),D('4'),AT)))
    t=Trade((l,));r=rows(t);storage=reader(monkeypatch,[t])
    result=source.load_strategy_entries(storage,tmp_path,r,(campaign(),))
    assert result[('APD',CID)][0]==t

@pytest.mark.parametrize('issue',['changed','missing','duplicate','price','campaign','carry','roll','extra'])
def test_unverified_or_ambiguous_entry_not_classified(monkeypatch,tmp_path,issue):
    t=Trade((leg('PUT'),));r=rows(t);storage=reader(monkeypatch,[t,t] if issue=='duplicate' else [t],valid=issue!='changed')
    if issue=='missing':storage=SimpleNamespace(exists=lambda *a:False)
    if issue=='price':r[0]['price']='4'
    if issue=='campaign':r[0]['campaign_id']='other'
    if issue=='carry':r.append(dict(r[0],action='Carried position'))
    if issue=='roll':r[0]['action']='Roll: open'
    if issue=='extra':r.append(dict(r[0],instrument=_serialize_instrument(leg().instrument)))
    assert source.load_strategy_entries(storage,tmp_path,r,(campaign(),))[('APD',CID)][0] is None

def test_legacy_entry_not_guessed(monkeypatch,tmp_path):
    storage=reader(monkeypatch,[Trade((leg('PUT'),))])
    c=campaign(cid='2026-05/CAMP-1');assert source.load_strategy_entries(storage,tmp_path,[],(c,))[('APD',c.campaign_id)][0] is None

def test_stock_coverage_accounts_for_preexisting_short_calls():
    t=Trade((leg(),));stock=dict(rows(Trade((leg('STOCK',side=Side.BUY,qty='100'),)))[0],occurred_at='2026-05-01T09:00:00',position_after='100')
    assert source._covered([stock],t)
    call=dict(rows(t)[0],occurred_at='2026-05-01T09:00:00',position_after='-1',campaign_id=CID+'2')
    assert not source._covered([stock,call],t)

def test_month_end_status_uses_saved_snapshot_and_publication(monkeypatch):
    from campaigniq.domain.lot import Lot
    from campaigniq.domain.lot_book import LotBook
    book=LotBook();book.seed(Lot('lot',leg('PUT').instrument,D('-1'),AT,None,campaign_id=CID))
    text=serialize_lot_book(period_end=date(2026,5,31),lot_book=book)
    storage=SimpleNamespace(read_text=lambda key:text)
    monkeypatch.setattr(source,'is_month_published_in_storage',lambda *a,**k:True)
    result=source.campaign_open_status(storage,date(2026,5,31),(campaign(),campaign(cid=CID+'2')))
    assert result[('APD',CID)] is True and result[('APD',CID+'2')] is False
    assert source.campaign_open_status(storage,date(2026,6,30),(campaign(),))=={}
    monkeypatch.setattr(source,'is_month_published_in_storage',lambda *a,**k:False)
    assert source.campaign_open_status(storage,date(2026,5,31),(campaign(),))=={}

def test_zero_currency_is_normalized():
    from campaigniq.ui.financial_format import display_money
    assert display_money(-0.0)==display_money(D('-0'))=='$0.00'

def test_ui_numeric_results_and_row_navigation():
    from campaigniq.ui.strategy_comparison_view import render_strategy_comparison
    class UI:
        session_state={}
        captured=[]
        def subheader(self,*a):pass
        def caption(self,*a):pass
        def markdown(self,*a):pass
        def dataframe(self,table,**kwargs):self.captured.append((table,kwargs))
    c=campaign();scope=ProfitConcentration((c,),D('100'),D('0'),D('100'),0,D('0'))
    ui=UI();render_strategy_comparison(ui,scope,{('APD',CID):(Trade((leg('PUT'),)),False,'verified')},{('APD',CID):False},date(2026,5,31))
    assert ui.captured[0][0].data['Realized P&L'].dtype.kind=='f'
    table,kwargs=ui.captured[-1];ui.session_state[kwargs['key']]={'selection':{'rows':[0]}};kwargs['on_select']()
    assert ui.session_state['campaigniq_primary_view']=='Campaigns' and ui.session_state['campaigniq_campaign_detail']=='2026-05/'+CID

def test_unassigned_month_end_lot_prevents_false_closed_status(monkeypatch):
    from campaigniq.domain.lot import Lot
    from campaigniq.domain.lot_book import LotBook
    book=LotBook();book.seed(Lot('lot',leg('PUT').instrument,D('-1'),AT,None,campaign_id=None))
    text=serialize_lot_book(period_end=date(2026,5,31),lot_book=book)
    storage=SimpleNamespace(read_text=lambda key:text)
    monkeypatch.setattr(source,'is_month_published_in_storage',lambda *a,**k:True)
    assert source.campaign_open_status(storage,date(2026,5,31),(campaign(),))[('APD',CID)] is None
