from datetime import date,datetime
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from campaigniq.analytics.strategy_comparison import classify_entry,classify_holdings
from campaigniq.analytics.profit_concentration import ConcentrationCampaign
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.trade import Trade
from campaigniq.domain.side import Side
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.persistence.lot_book_store import _serialize_instrument
import campaigniq.ui.strategy_evidence as source
AT=datetime(2026,2,2,11,44,7);CID='2026-02:CSCO:CAMP-000001'

def option(symbol,kind,strike,expiry=date(2026,4,17),quantity='1',side=Side.SELL):
    return Leg(OptionContract(symbol,expiry,D(strike),OptionType[kind]),side,PositionEffect.OPEN,(Execution(D(quantity),D('3'),AT),))

@pytest.mark.parametrize('symbol,call_strike,put_strike',[
    ('ACN','185','235'),('DELL','130','160'),('GLW','110','155'),('IBM','235','280'),
    ('DELL','150','200'),('GLW','115','165')])
def test_six_review_shapes_are_inverted_short_strangles(symbol,call_strike,put_strike):
    call=option(symbol,'CALL',call_strike);put=option(symbol,'PUT',put_strike)
    assert classify_entry(Trade((call,put)))=='Inverted short strangle'
    assert classify_holdings([(call.instrument,D('-1')),(put.instrument,D('-1'))])=='Inverted short strangle'

@pytest.mark.parametrize('issue',['long_put','different_expiry','different_quantity'])
def test_inverted_label_requires_same_expiry_quantity_and_both_short(issue):
    call=option('DELL','CALL','130');put=option('DELL','PUT','160',expiry=date(2026,5,15) if issue=='different_expiry' else date(2026,4,17),quantity='2' if issue=='different_quantity' else '1',side=Side.BUY if issue=='long_put' else Side.SELL)
    assert classify_entry(Trade((call,put)))=='Unclassified'


def buywrite(q):
    stock=Leg(Instrument('CSCO'),Side.BUY,PositionEffect.OPEN,(Execution(D(q)*100,D('81.02'),AT),))
    call=Leg(OptionContract('CSCO',date(2026,3,20),D('75'),OptionType.CALL),Side.SELL,PositionEffect.OPEN,(Execution(-D(q),D('7.63'),AT),))
    return Trade((call,stock))

def journal(t):
    return [dict(source='Published position journal',period_start='2026-02-01',campaign_id=CID,occurred_at=AT.isoformat(),action='Open / add',
                 instrument=_serialize_instrument(l.instrument),quantity_change=str(l.executions[0].quantity),price=str(l.executions[0].execution_price),position_after='0',lots=[]) for l in t.legs]

def setup(monkeypatch,trades,valid=True):
    monkeypatch.setattr(source,'_source_matches_manifest',lambda *a:valid)
    monkeypatch.setattr(source,'ThinkorswimTradeReader',lambda *a:SimpleNamespace(read=lambda *a,**k:trades))
    return SimpleNamespace(exists=lambda *a:True)

def load(storage,evidence):
    c=ConcentrationCampaign(CID,'CSCO',D('1673.37'),'2026-03/'+CID)
    return source.load_observed_strategies(storage,'.',evidence,(c,),date(2026,9,30),{})[('CSCO',CID)]


def test_three_plus_two_contract_buywrite_matches_five_calls_and_five_hundred_shares(monkeypatch):
    ts=[buywrite('3'),buywrite('2')];storage=setup(monkeypatch,ts);evidence=[r for t in ts for r in journal(t)]
    observed=load(storage,evidence)
    assert observed.strategy=='Covered call' and observed.basis=='Verified opening execution groups'
    assert '2 complete' in observed.reason

@pytest.mark.parametrize('issue',['missing_stock','wrong_price','wrong_call_quantity','wrong_campaign','source_changed','duplicate_source'])
def test_split_opening_group_verification_blocks_missing_duplicate_changed_facts(monkeypatch,issue):
    ts=[buywrite('3'),buywrite('2')];evidence=[r for t in ts for r in journal(t)]
    if issue=='missing_stock':evidence.pop()
    if issue=='wrong_price':evidence[-1]['price']='99'
    if issue=='wrong_call_quantity':evidence[0]['quantity_change']='-4'
    if issue=='wrong_campaign':evidence[-1]['campaign_id']='other'
    if issue=='duplicate_source':ts.append(buywrite('3'))
    storage=setup(monkeypatch,ts,valid=issue!='source_changed')
    assert load(storage,evidence).strategy=='Unclassified'


def test_separate_stock_and_call_orders_are_not_joined_even_at_same_timestamp(monkeypatch):
    t=buywrite('5');trades=[Trade((t.legs[0],)),Trade((t.legs[1],))];evidence=journal(t)
    storage=setup(monkeypatch,trades)
    assert load(storage,evidence).strategy=='Unclassified'


def test_different_group_ratios_are_not_merged(monkeypatch):
    ts=[buywrite('3'),buywrite('2')];leg=ts[1].legs[1]
    ts[1]=Trade((ts[1].legs[0],Leg(leg.instrument,leg.side,leg.position_effect,(Execution(D('100'),D('81.02'),AT),))))
    evidence=[r for t in ts for r in journal(t)];storage=setup(monkeypatch,ts)
    assert load(storage,evidence).strategy=='Unclassified'
