from datetime import date,datetime
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.trade import Trade
from campaigniq.domain.side import Side
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.analytics.profit_concentration import ConcentrationCampaign
from campaigniq.persistence.lot_book_store import _serialize_instrument
import campaigniq.ui.strategy_evidence as source
import campaigniq.ui.roll_evidence as rolls
AT=datetime(2026,9,14,9,40,47);CID='2026-09:MCD:CAMP-000017'

def trade(close_price='3.37'):
    old=OptionContract('MCD',date(2026,10,2),D('255'),OptionType.PUT)
    new=OptionContract('MCD',date(2026,10,16),D('255'),OptionType.PUT)
    return Trade((Leg(old,Side.BUY,PositionEffect.CLOSE,(Execution(D('1'),D(close_price),AT),)),
                  Leg(new,Side.SELL,PositionEffect.OPEN,(Execution(D('-1'),D('4.69'),AT),))))

def journal(t):
    return [dict(source='Published position journal',period_start='2026-09-01',occurred_at=AT.isoformat(),instrument=_serialize_instrument(l.instrument),
                 action='Roll: open' if l.position_effect is PositionEffect.OPEN else 'Roll: close',
                 campaign_id=CID if l.position_effect is PositionEffect.OPEN else '2026-08:MCD:CAMP-1',
                 quantity_change='-1' if l.side is Side.SELL else '1',price=str(l.executions[0].execution_price),lots=[])
            for l in t.legs]

def configure(monkeypatch,ts,valid=True):
    for module in (source,rolls):
        monkeypatch.setattr(module,'_source_matches_manifest',lambda *a:valid)
        monkeypatch.setattr(module,'ThinkorswimTradeReader',lambda *a:SimpleNamespace(read=lambda *a,**k:ts))
    return SimpleNamespace(exists=lambda *a:True)

def observed(storage,evidence):
    c=ConcentrationCampaign(CID,'MCD',D('-2720.67'),'2026-09/'+CID)
    return source.load_observed_strategies(storage,'.',evidence,(c,),date(2026,9,30),{})[('MCD',CID)]

@pytest.mark.parametrize('prices',[('3.37','3.36'),('3.37','3.37')])
def test_complete_two_fill_roll_observed_as_short_put(monkeypatch,prices):
    ts=[trade(p) for p in prices];storage=configure(monkeypatch,ts);evidence=[r for t in ts for r in journal(t)]
    r=observed(storage,evidence)
    assert r.strategy=='Short put' and r.basis=='Verified roll execution groups'
    assert '2 complete' in r.reason

@pytest.mark.parametrize('issue',['close_missing','open_missing','wrong_price','wrong_campaign','duplicate_source','changed_source'])
def test_incomplete_or_duplicate_evidence_not_accepted(monkeypatch,issue):
    ts=[trade(),trade('3.36')];evidence=[r for t in ts for r in journal(t)]
    if issue=='close_missing':evidence.pop(0)
    if issue=='open_missing':evidence.pop(1)
    if issue=='wrong_price':evidence[0]['price']='99'
    if issue=='wrong_campaign':evidence[-1]['campaign_id']='other'
    if issue=='duplicate_source':ts.append(trade())
    storage=configure(monkeypatch,ts,valid=issue!='changed_source')
    assert observed(storage,evidence).strategy=='Unclassified'


def test_aggregated_journal_preserves_total_fill_quantities_and_weighted_prices(monkeypatch):
    ts=[trade(),trade('3.36')];evidence=journal(ts[0]);evidence[0].update(quantity_change='2',price='3.365');evidence[1]['quantity_change']='-2'
    storage=configure(monkeypatch,ts)
    assert observed(storage,evidence).strategy=='Short put'


def test_single_source_group_is_not_duplicated_to_fill_journal(monkeypatch):
    t=trade();evidence=journal(t)+journal(t);storage=configure(monkeypatch,[t])
    assert observed(storage,evidence).strategy=='Unclassified'

def test_remaining_review_exports_real_first_observation_legs(monkeypatch):
    import json
    from campaigniq.analytics.strategy_comparison import compare_strategies,unclassified_review
    t=trade();evidence=journal(t);evidence[0]['quantity_change']='2'
    storage=configure(monkeypatch,[t]);r=observed(storage,evidence)
    assert r.strategy=='Unclassified'
    legs=json.loads(r.observed_legs)
    assert len(legs)==1 and legs[0]['quantity_change']=='-1'
    c=ConcentrationCampaign(CID,'MCD',D('-2720.67'),'2026-09/'+CID)
    _,details=compare_strategies((c,),{}, {},observed_evidence={('MCD',CID):r})
    _,review=unclassified_review(details)
    assert json.loads(review[0]['First observation journal legs'])==legs
