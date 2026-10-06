from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace
from copy import deepcopy
import pytest
from campaigniq.analytics.profit_concentration import summarize_profit_concentration, underlying_contributions
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument

MAY=(date(2026,5,1),date(2026,5,31));JUNE=(date(2026,6,1),date(2026,6,30))
def record(pnl,cid='2026-05:APD:CAMP-1',symbol='APD',day=date(2026,5,20),basis='1000'):
    r=RealizedGainLossRecord(day,Instrument(symbol),D('1'),D('1'),D('1000')+D(pnl),D('1000'),D(pnl),'FIFO','ST',D('0'))
    return RealizedAttribution(r,(LotAllocation('lot',D('1'),D(basis),campaign_id=cid),))

def test_top_winners_gross_denominator_and_without_largest():
    s=summarize_profit_concentration({MAY:[record('100'),record('50',cid='C2'),record('-120',cid='C3')]})
    assert s.gross_profits==150 and s.gross_losses==120 and s.net_realized==30
    count,pnl,share,remainder=s.top(1)
    assert count==1 and pnl==100 and share==D('100')/150 and remainder==-70
    assert s.top(5)==(2,D('150'),D('1'),D('-120'))

@pytest.mark.parametrize('values',[[-100], [0], [100,-100], [100,-200], []])
def test_zero_negative_and_empty_scopes(values):
    s=summarize_profit_concentration({MAY:[record(str(v),cid='C'+str(i)) for i,v in enumerate(values)]})
    assert s.net_realized==sum(values)
    assert s.top(1)[2] is None if not any(v>0 for v in values) else s.top(1)[2]==1

def test_scoped_campaign_offsets_monthly_closes_before_classification():
    s=summarize_profit_concentration({MAY:[record('100')],JUNE:[record('-80',day=date(2026,6,15))]})
    assert len(s.campaigns)==1 and s.gross_profits==20 and s.gross_losses==0
    assert s.campaigns[0].drilldown_id=='2026-06/2026-05:APD:CAMP-1'

def test_selected_range_does_not_pull_other_months():
    s=summarize_profit_concentration({JUNE:[record('-80',day=date(2026,6,15))]})
    assert s.net_realized==-80 and not s.winners

def test_legacy_ids_remain_separate_across_months_and_symbols():
    s=summarize_profit_concentration({MAY:[record('100',cid='C1'),record('50',cid='C1',symbol='GS')],JUNE:[record('-80',cid='C1',day=date(2026,6,15))]})
    assert len(s.campaigns)==3 and s.gross_profits==150 and s.gross_losses==80
    assert '2026-05/C1@APD' in {r.drilldown_id for r in s.campaigns}

def test_shared_unknown_unreconciled_futures_and_invalid_namespace_excluded():
    good=record('100');shared=RealizedAttribution(good.record,(LotAllocation('a',D('0.5'),D('500'),campaign_id='C1'),LotAllocation('b',D('0.5'),D('500'),campaign_id='C2')))
    items=[shared,record('20',cid=None),record('30',basis='999'),record('40',symbol='/ES',cid='C4'),record('50',cid='2026-05:GS:CAMP-1')]
    s=summarize_profit_concentration({MAY:[good,*items]})
    assert s.excluded_records==5 and s.excluded_pnl==240 and s.net_realized==100

def test_out_of_period_record_not_duplicated():
    r=record('100');s=summarize_profit_concentration({MAY:[r],JUNE:[r]})
    assert s.net_realized==100 and s.excluded_records==0

def test_underlying_components_reconcile_and_no_mutation():
    monthly={MAY:[record('100'),record('-20',cid='C2'),record('50',symbol='GS',cid='C3')]};before=deepcopy(monthly)
    s=summarize_profit_concentration(monthly);rows=underlying_contributions(s)
    assert sum(r['net'] for r in rows)==s.net_realized
    assert rows[0]['underlying']=='APD' and rows[0]['profits']==100 and rows[0]['losses']==20
    assert monthly==before

def test_ties_sort_deterministically():
    s=summarize_profit_concentration({MAY:[record('50',cid='C2'),record('50',cid='C1')]})
    assert [r.campaign_id for r in s.winners]==['2026-05/C1','2026-05/C2']

def test_ui_selection_keeps_numeric_values_and_routes_selected_campaign():
    from campaigniq.ui.profit_concentration_view import _campaign_table
    captured=[];state={}
    ui=SimpleNamespace(session_state=state,dataframe=lambda table,**kwargs:captured.append((table,kwargs)))
    s=summarize_profit_concentration({MAY:[record('100'),record('50',cid='C2')]})
    _campaign_table(ui,s.winners,s.gross_profits,label='profits')
    table,kwargs=captured[0];assert table.data['Realized P&L'].dtype.kind=='f'
    state[kwargs['key']]={'selection':{'rows':[1]}};kwargs['on_select']()
    assert state['campaigniq_primary_view']=='Campaigns' and state['campaigniq_campaign_detail']=='2026-05/C2'
    assert state['campaigniq_campaign_symbol']=='APD'

def test_view_handles_no_winners_and_no_records():
    from campaigniq.ui.profit_concentration_view import render_profit_concentration
    class UI:
        session_state={}
        def subheader(self,*a):pass
        def caption(self,*a):pass
        def info(self,*a):pass
        def markdown(self,*a):pass
        def metric(self,*a):pass
        def columns(self,n):return [self]*n
        def dataframe(self,*a,**k):pass
    for records in ([],[record('-100')],[record('0')]):render_profit_concentration(UI(),summarize_profit_concentration({MAY:records}))
