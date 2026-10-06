"""Read-only strategy comparisons with visible classification evidence."""
import hashlib
import json
import pandas as pd
from campaigniq.analytics.strategy_comparison import compare_strategies
from campaigniq.ui.financial_format import display_money
from campaigniq.ui.table_layout import render_dataframe,selection_table_key
from campaigniq.ui.analytics_navigation import remember_return,publish_route


def render_strategy_comparison(ui,scope,entries,status,period_end):
    ui.subheader('Strategy Comparison')
    ui.caption(f'Strategy at campaign entry; equity/options only. Realized closes within the selected reporting range. Position status is as of {period_end:%b %d, %Y}. Subsequent rolls and assignments can change the strategy. These are dollar outcomes, not returns on capital or risk-adjusted rankings.')
    if not scope.campaigns:
        ui.info('No eligible realized campaigns are available for strategy comparison.');return
    groups,details=compare_strategies(scope.campaigns,entries,status)
    rows=[{'Entry strategy':r['strategy'],'Campaigns':r['campaigns'],'Closed':r['closed'],'Open':r['open'],
           'Status unknown':r['unknown'],'Closed win rate':float(r['win_rate']*100) if r['win_rate'] is not None else None,
           'Realized P&L':float(r['realized']),'Average realized P&L':float(r['average']),'Median realized P&L':float(r['median'])} for r in groups]
    frame=pd.DataFrame(rows).sort_values('Realized P&L',ascending=False).reset_index(drop=True)
    styled=frame.style.format({'Realized P&L':display_money,'Average realized P&L':display_money,
                              'Median realized P&L':display_money,'Closed win rate':'{:.1f}%'},na_rep='Unavailable')
    render_dataframe(ui,styled,hide_index=True,use_container_width=True)
    ui.caption('Win rate is the fraction of confirmed closed campaigns with positive realized P&L in the selected range; breakevens stay in the denominator. It is not a lifetime win rate when the range omits earlier closes. Totals, averages, and medians include all eligible campaigns with realized closes, including open and status-unknown campaigns. Campaigns without any selected-period realized closes are absent.')
    ui.caption('Short put does not establish cash collateral. Covered call requires a matching stock purchase in the opening order or verified prior stock coverage after allowing for existing short calls. Stock entry describes an initial stock order, not a promise that the campaign remained stock-only. Unclassified campaigns remain visible.')
    ui.markdown('##### Campaign classifications')
    details=tuple(sorted(details,key=lambda r:(r['strategy'],-r['campaign'].realized_pnl,r['campaign'].campaign_id)))
    rows=[{'Entry strategy':r['strategy'],'Underlying':r['campaign'].underlying,'Campaign':r['campaign'].campaign_id,
           'Realized P&L':float(r['campaign'].realized_pnl),'Position status':'Open' if r['open'] is True else 'Closed' if r['open'] is False else 'Unavailable',
           'Classification evidence':r['evidence']} for r in details]
    table=pd.DataFrame(rows).style.format({'Realized P&L':display_money})
    fingerprint=hashlib.sha256(json.dumps([(r['campaign'].drilldown_id,r['strategy'],str(r['campaign'].realized_pnl)) for r in details]).encode()).hexdigest()[:16]
    key=selection_table_key(ui,f'campaigniq_strategy_rows_{period_end}_{fingerprint}')
    def selected():
        indices=ui.session_state.get(key,{}).get('selection',{}).get('rows',[])
        if not indices or not isinstance(indices[0],int) or not 0<=indices[0]<len(details):return
        campaign=details[indices[0]]['campaign']
        if hasattr(ui,'query_params'):remember_return(ui)
        ui.session_state.update(campaigniq_primary_view='Campaigns',campaigniq_campaign_month='All months',
                                campaigniq_campaign_symbol=campaign.underlying,campaigniq_campaign_result='All results',campaigniq_campaign_detail=campaign.drilldown_id)
        if hasattr(ui,'query_params'):publish_route(ui)
    render_dataframe(ui,table,hide_index=True,use_container_width=True,height=420,key=key,on_select=selected,selection_mode='single-row')
    ui.caption('Click a campaign row to inspect its details. A multi-month campaign opens its latest included reporting-month detail; this table reports its combined selected-range result. The underlying records use the same reconciliation and attribution exclusions as Profit Concentration.')
    if scope.excluded_records:
        ui.warning(f'{scope.excluded_records} broker record(s), net {display_money(scope.excluded_pnl)}, are excluded from this comparison.')
