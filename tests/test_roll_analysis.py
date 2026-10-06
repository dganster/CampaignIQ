from datetime import date, datetime
from decimal import Decimal as D
from types import SimpleNamespace
from copy import deepcopy
import pytest
from campaigniq.analytics.roll_analysis import analyze_roll
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.side import Side
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.trade import Trade
from campaigniq.persistence.lot_book_store import _serialize_instrument
import campaigniq.ui.roll_evidence as evidence_module
from campaigniq.ui.financial_format import display_money

AT=datetime(2026,5,1,10,0,0)


def roll(close_price='2',open_price='3',*,quantity='1',symbol='APD'):
    old=OptionContract(symbol,date(2026,6,18),D('270'),OptionType.CALL)
    new=OptionContract(symbol,date(2026,7,17),D('280'),OptionType.CALL)
    return Trade((Leg(old,Side.BUY,PositionEffect.CLOSE,(Execution(D(quantity),D(close_price),AT),)),
                  Leg(new,Side.SELL,PositionEffect.OPEN,(Execution(D(quantity),D(open_price),AT),))))


def rows(trade):
    result=[]
    for index,leg in enumerate(trade.legs):
        q=sum(abs(e.quantity) for e in leg.executions)
        price=sum(abs(e.quantity)*e.execution_price for e in leg.executions)/q
        result.append(dict(period_start='2026-05-01',occurred_at=min(e.executed_at for e in leg.executions).isoformat(),
            action='Roll: close' if leg.position_effect is PositionEffect.CLOSE else 'Roll: open',instrument=_serialize_instrument(leg.instrument),
            quantity_change=str(q if leg.side is Side.BUY else -q),price=str(price),campaign_id=f'2026-04:APD:CAMP-{index}',
            lots=[dict(lot_id=f'lot-{index}',quantity=str(q))],source='Published position journal'))
    return result


def verified_reader(monkeypatch,trades):
    monkeypatch.setattr(evidence_module,'_source_matches_manifest',lambda *a:True)
    monkeypatch.setattr(evidence_module,'ThinkorswimTradeReader',lambda *a:SimpleNamespace(read=lambda *a,**k:trades))
    return SimpleNamespace(exists=lambda key:True)


def test_credit_roll_contract_changes():
    result=analyze_roll(roll())
    assert result.close_cash_flow==D('-200') and result.open_cash_flow==D('300')
    assert result.net_cash_flow==D('100')
    assert result.strike_change==D('10') and result.expiration_change_days==29
    assert result.contract_quantity_change==0


def test_debit_roll():
    assert analyze_roll(roll(close_price='4',open_price='1')).net_cash_flow==D('-300')


def test_partial_fills_use_each_execution_price():
    trade=roll()
    partial=Leg(trade.legs[0].instrument,Side.BUY,PositionEffect.CLOSE,(Execution(D('1'),D('1'),AT),Execution(D('1'),D('2'),AT)))
    opened=Leg(trade.legs[1].instrument,Side.SELL,PositionEffect.OPEN,(Execution(D('2'),D('3'),AT),))
    result=analyze_roll(Trade((partial,opened)))
    assert result.close_cash_flow==D('-300') and result.open_cash_flow==D('600')


def test_long_option_roll_cash_signs():
    trade=roll()
    closed=Leg(trade.legs[0].instrument,Side.SELL,PositionEffect.CLOSE,trade.legs[0].executions)
    opened=Leg(trade.legs[1].instrument,Side.BUY,PositionEffect.OPEN,trade.legs[1].executions)
    assert analyze_roll(Trade((closed,opened))).net_cash_flow==D('-100')


def test_missing_close_or_futures_multiplier_rejected():
    with pytest.raises(ValueError,match='requires explicitly'):
        analyze_roll(Trade((roll().legs[1],)))
    with pytest.raises(ValueError,match='futures'):
        analyze_roll(roll(symbol='/ES'))


def test_verified_source_group_links_counterpart_in_new_campaign(tmp_path,monkeypatch):
    trade=roll();entries=rows(trade);storage=verified_reader(monkeypatch,[trade])
    result,warnings=evidence_module.load_campaign_rolls(storage,tmp_path,entries,[entries[0]])
    assert len(result)==1 and result[0].net_cash_flow==D('100') and warnings==()


def test_legs_can_have_different_execution_times_in_same_verified_order(tmp_path,monkeypatch):
    trade=roll();leg=trade.legs[1]
    late=Leg(leg.instrument,leg.side,leg.position_effect,(Execution(D('1'),D('3'),datetime(2026,5,1,10,0,1)),))
    trade=Trade((trade.legs[0],late));entries=rows(trade);storage=verified_reader(monkeypatch,[trade])
    result,warnings=evidence_module.load_campaign_rolls(storage,tmp_path,entries,[entries[0]])
    assert len(result)==1 and not warnings


def test_changed_archived_source_cannot_establish_roll(tmp_path,monkeypatch):
    trade=roll();entries=rows(trade);storage=verified_reader(monkeypatch,[trade])
    monkeypatch.setattr(evidence_module,'_source_matches_manifest',lambda *a:False)
    result,warnings=evidence_module.load_campaign_rolls(storage,tmp_path,entries,[entries[0]])
    assert not result and any('provenance' in message for message in warnings)


def test_unmatched_or_ambiguous_order_not_counted(tmp_path,monkeypatch):
    trade=roll();entries=rows(trade);storage=verified_reader(monkeypatch,[trade,trade])
    result,warnings=evidence_module.load_campaign_rolls(storage,tmp_path,entries,[entries[0]])
    assert not result and any('multiple source orders' in message for message in warnings)
    storage=verified_reader(monkeypatch,[trade]);entries[1]['quantity_change']='-2'
    result,warnings=evidence_module.load_campaign_rolls(storage,tmp_path,entries,[entries[0]])
    assert not result and warnings


def test_unrelated_campaign_roll_is_not_pulled_in(tmp_path,monkeypatch):
    trade=roll();entries=rows(trade);storage=verified_reader(monkeypatch,[trade])
    unrelated=dict(entries[0],action='Open / add')
    result,warnings=evidence_module.load_campaign_rolls(storage,tmp_path,entries,[unrelated])
    assert not result and not warnings


def test_negative_currency_preserves_numeric_table_for_sorting():
    from campaigniq.ui.financial_format import render_money_table
    captured=[]
    ui=SimpleNamespace(session_state={},dataframe=lambda table,**k:captured.append(table))
    render_money_table(ui,[{'Amount':-975.27},{'Amount':1000}],('Amount',))
    assert display_money(D('-975.27'))=='-$975.27'
    assert captured[0].data['Amount'].dtype.kind=='f'
    assert '-$975.27' in captured[0].to_html()

def attribution(record,lot_id,cid):
    from campaigniq.domain.lot_attribution import RealizedAttribution
    from campaigniq.domain.lot_allocation import LotAllocation
    return RealizedAttribution(record,(LotAllocation(lot_id,record.quantity,record.cost_basis,campaign_id='2026-06/'+cid),))


def broker(instrument,day,quantity='1'):
    from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
    return RealizedGainLossRecord(day,instrument,D(quantity),D('1'),D('100'),D('100'),D('0'),'FIFO','SHORT')


def test_short_call_direction_uses_consumed_lot_and_market_holiday():
    trade=roll();row=rows(trade)[0];row['occurred_at']='2026-06-18T10:00:00'
    result=evidence_module.closed_side_from_journal(attribution(broker(trade.legs[0].instrument,date(2026,6,22)),'lot-0',row['campaign_id']),[row])
    assert result=='Short'


def test_long_stock_assignment_direction_uses_next_month_posting():
    from campaigniq.domain.value_objects.instrument import Instrument
    row=dict(source='Published position journal',action='Assignment',instrument=_serialize_instrument(Instrument('APD')),
             occurred_at='2026-07-01T00:00:00',campaign_id='2026-04:APD:CAMP-1',quantity_change='-100',lots=[dict(lot_id='stock',quantity='100')])
    record=broker(Instrument('APD'),date(2026,6,30),'100')
    assert evidence_module.closed_side_from_journal(attribution(record,'stock',row['campaign_id']),[row])=='Long'
    row['lots'][0]['quantity']='99'
    assert evidence_module.closed_side_from_journal(attribution(record,'stock',row['campaign_id']),[row])=='Unavailable'


def test_wrong_campaign_or_lot_cannot_establish_direction():
    trade=roll();row=rows(trade)[0];record=broker(trade.legs[0].instrument,AT.date())
    assert evidence_module.closed_side_from_journal(attribution(record,'wrong',row['campaign_id']),[row])=='Unavailable'
    assert evidence_module.closed_side_from_journal(attribution(record,'lot-0','2026-04:APD:CAMP-9'),[row])=='Unavailable'

def test_missing_provenance_does_not_use_unverified_original_order(tmp_path,monkeypatch):
    trade=roll();entries=rows(trade);verified_reader(monkeypatch,[trade])
    result,warnings=evidence_module.load_campaign_rolls(SimpleNamespace(exists=lambda key:False),tmp_path,entries,[entries[0]])
    assert not result and any('provenance' in message for message in warnings)


def test_split_close_allocations_match_whole_source_leg(tmp_path,monkeypatch):
    trade=roll(quantity='3');entries=rows(trade)
    second=dict(entries[0],quantity_change='2',campaign_id='2026-04:APD:CAMP-99')
    entries[0]['quantity_change']='1';entries.append(second)
    storage=verified_reader(monkeypatch,[trade])
    result,warnings=evidence_module.load_campaign_rolls(storage,tmp_path,entries,[entries[0]])
    assert len(result)==1 and result[0].net_cash_flow==D('300') and not warnings


def test_roll_view_shows_cumulative_gross_credit_and_debit():
    from contextlib import nullcontext
    from campaigniq.ui.roll_analysis_view import render_roll_analysis
    class UI:
        session_state={}
        def __init__(self):self.metrics=[];self.tables=[];self.captions=[]
        def markdown(self,text):pass
        def caption(self,text):self.captions.append(text)
        def info(self,text):pass
        def warning(self,text):pass
        def columns(self,count):return [self]*count
        def metric(self,label,value):self.metrics.append((label,value))
        def dataframe(self,table,**kwargs):self.tables.append(table)
        def expander(self,label):return nullcontext()
    ui=UI();rolls=[analyze_roll(roll()),analyze_roll(roll(close_price='4',open_price='1'))]
    render_roll_analysis(ui,rolls,())
    frame=ui.tables[0].data
    assert frame['Net before fees'].tolist()==[100,-300]
    assert frame['Cumulative before fees'].tolist()==[100,-200]
    assert ('Gross roll credits','$100.00') in ui.metrics and ('Gross roll debits paid','$300.00') in ui.metrics
    assert '-$300.00' in ui.tables[0].to_html()
    assert any('not realized P&L' in text for text in ui.captions)
