from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from campaigniq.analytics.campaign_economics import campaign_record_scope, summarize_campaign_economics
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.value_objects.instrument import Instrument


def attribution(cid, day, pnl, *, option=False, adjustment='0', symbol='APD'):
    instrument=OptionContract(symbol,date(2026,7,17),D('270'),OptionType.CALL) if option else Instrument(symbol)
    basis=D('1000');gain=D(pnl);adj=D(adjustment)
    record=RealizedGainLossRecord(day,instrument,D('1'),D('1'),basis+gain-adj,basis,gain,'FIFO','ST',adj)
    return RealizedAttribution(record,(LotAllocation('lot',D('1'),basis,campaign_id=cid),))


def trade(action, quantity, price, *, option=True):
    return dict(action=action,quantity_change=quantity,price=price,instrument={'type':'option' if option else 'instrument','underlying':'APD'})


def test_stock_option_components_match_broker_total_without_adding_premiums():
    records=[attribution('2026-04:APD:CAMP-1',date(2026,6,30),'-975.27'),attribution('2026-04:APD:CAMP-1',date(2026,6,22),'2635.60',option=True)]
    entries=[trade('Open / add','-1','30'),trade('Close / reduce','1','3')]
    result=summarize_campaign_economics(records,entries)
    assert result.stock_realized==D('-975.27') and result.option_realized==D('2635.60')
    assert result.total_realized==D('1660.33')
    assert result.observed_option_cash_flow==D('2700')
    assert result.total_realized==sum(a.record.gain_loss for a in records)
    assert result.reconciled


def test_loss_adjustments_reconcile_reported_arithmetic():
    result=summarize_campaign_economics([attribution('C1',date(2026,6,30),'-50',adjustment='20')])
    assert result.reported_proceeds-result.reported_basis+result.reported_adjustments==result.total_realized


def test_option_cash_flows_distinguish_short_and_long_legs_and_rolls():
    rows=[trade('Open / add','-2','4'),trade('Roll: close','2','1'),trade('Roll: open','-2','5'),
          trade('Open / add','2','0.5'),trade('Close / reduce','-2','0.75')]
    result=summarize_campaign_economics([],rows)
    assert result.short_premiums_received==D('1800')
    assert result.short_option_buybacks==D('200')
    assert result.long_option_purchases==D('100') and result.long_option_sale_receipts==D('150')
    assert result.observed_option_cash_flow==D('1650')
    assert result.total_realized==0


def test_carried_positions_and_events_do_not_invent_cash_flows():
    rows=[trade('Carried position','-1',None),trade('Assignment','1',None),trade('Expiration','1',None),trade('Open / add','100','270',option=False),trade('Close / reduce','1',None)]
    result=summarize_campaign_economics([],rows)
    assert result.observed_option_cash_flow==0
    assert result.carried_position_count==1 and result.missing_option_trade_prices==1


def test_futures_option_multiplier_is_not_assumed():
    row=trade('Open / add','-1','12');row['instrument']['underlying']='/ES'
    result=summarize_campaign_economics([],[row])
    assert result.observed_option_trade_count==0 and result.missing_option_trade_prices==1


def test_scoped_identity_links_realized_closes_across_months():
    cid='2026-04:APD:CAMP-1'
    may=attribution(cid,date(2026,5,20),'100',option=True)
    june=attribution(cid,date(2026,6,30),'-20')
    unrelated=attribution('2026-06:APD:CAMP-1',date(2026,6,30),'500')
    monthly={(date(2026,5,1),date(2026,5,31)):(may,), (date(2026,6,1),date(2026,6,30)):(june,unrelated)}
    records,lifetime=campaign_record_scope(monthly,'2026-06/'+cid)
    assert lifetime and [a.record.gain_loss for a in records]==[D('100'),D('-20')]


def test_legacy_local_ids_do_not_merge_across_months():
    monthly={(date(2026,5,1),date(2026,5,31)):(attribution('CAMP-1',date(2026,5,20),'100'),),
             (date(2026,6,1),date(2026,6,30)):(attribution('CAMP-1',date(2026,6,20),'-20'),)}
    records,lifetime=campaign_record_scope(monthly,'2026-06/CAMP-1')
    assert not lifetime and len(records)==1 and records[0].record.gain_loss==D('-20')


def test_shared_and_unassigned_closes_are_excluded():
    good=attribution('2026-04:APD:CAMP-1',date(2026,6,20),'100')
    shared=RealizedAttribution(good.record,(LotAllocation('a',D('0.5'),D('500'),campaign_id='2026-04:APD:CAMP-1'),LotAllocation('b',D('0.5'),D('500'),campaign_id=None)))
    records,_=campaign_record_scope({(date(2026,6,1),date(2026,6,30)):(shared,)},'2026-06/2026-04:APD:CAMP-1')
    assert records==()


def test_out_of_period_broker_records_are_not_double_counted():
    record=attribution('2026-04:APD:CAMP-1',date(2026,6,20),'100')
    monthly={(date(2026,5,1),date(2026,5,31)):(record,), (date(2026,6,1),date(2026,6,30)):(record,)}
    records,_=campaign_record_scope(monthly,'2026-06/2026-04:APD:CAMP-1')
    assert len(records)==1


def test_partial_trade_quantities_use_retained_exact_decimals():
    result=summarize_campaign_economics([],[trade('Open / add','-3','1.125')])
    assert result.short_premiums_received==D('337.500')


def test_unreconciled_broker_fact_stays_visible_and_flagged():
    a=attribution('C1',date(2026,6,20),'100')
    a=RealizedAttribution(a.record,(LotAllocation('lot',D('1'),None,campaign_id='C1'),))
    result=summarize_campaign_economics([a])
    assert result.total_realized==D('100') and not result.reconciled

def test_same_scoped_id_cannot_pull_in_another_underlying():
    cid='2026-04:APD:CAMP-1'
    wrong=attribution(cid,date(2026,5,20),'100',symbol='GS')
    records,_=campaign_record_scope({(date(2026,5,1),date(2026,5,31)):(wrong,)},'2026-05/'+cid)
    assert not records


def test_verified_original_opening_suppresses_carried_cash_gap_warning():
    opening=trade('Open / add','-1','5');opening['occurred_at']='2026-04-01T10:00:00'
    carried=trade('Carried position','-1',None);carried['opening_refs']=[dict(opened_at=opening['occurred_at'])]
    result=summarize_campaign_economics([],[opening,carried])
    assert result.carried_position_count==0 and result.short_premiums_received==D('500')

def test_economics_view_keeps_broker_profit_separate_from_cash_flows():
    from contextlib import nullcontext
    from campaigniq.ui.campaign_economics_view import render_campaign_economics
    class UI:
        session_state={}
        column_config=SimpleNamespace(NumberColumn=lambda *a,**k:None)
        def __init__(self):
            self.metrics=[];self.tables=[];self.messages=[]
        def markdown(self,text): self.messages.append(text)
        def caption(self,text): self.messages.append(text)
        def info(self,text): self.messages.append(text)
        def warning(self,text): self.messages.append(text)
        def columns(self,count): return [self]*count
        def metric(self,label,value): self.metrics.append((label,value))
        def dataframe(self,table,**kwargs): self.tables.append(table)
        def expander(self,label): return nullcontext()
    ui=UI();cid='2026-04:APD:CAMP-1'
    current=[attribution(cid,date(2026,6,30),'-20')]
    full=[attribution(cid,date(2026,5,20),'100',option=True),*current]
    render_campaign_economics(ui,selected_records=current,campaign_records=full,
        campaign_entries=[trade('Open / add','-1','10')],lifetime_scope=True,reporting_month='2026-06')
    assert ui.metrics[0]==('Campaign realized P&L','$80.00')
    assert ui.tables[0][-1]['Realized P&L']==80
    assert ui.tables[-1][-1]['Amount']==1000
    assert any('Separate trading fees: unavailable' in message for message in ui.messages)
    assert any('2026-06: $-20.00' in message for message in ui.messages)


def test_economics_is_on_the_shared_campaign_detail_route_before_history():
    from pathlib import Path
    source=Path('src/campaigniq/ui/dashboard.py').read_text()
    section=source[source.index('    if selected is not None:'):]
    assert section.index('render_campaign_economics(st,') < section.index('st.markdown("#### Position history")')
    assert 'campaign_record_scope(history_monthly_attributions, selected_id)' in section
    assert 'if selected_id not in forex_campaign_records:' in section
