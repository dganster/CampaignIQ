"""Read-only strategy comparisons with visible classification evidence."""
import hashlib
import json
import pandas as pd
from campaigniq.analytics.strategy_comparison import compare_strategies, unclassified_review
from campaigniq.ui.financial_format import display_money, render_money_table
from campaigniq.ui.table_layout import render_dataframe,selection_table_key
from campaigniq.ui.analytics_navigation import remember_return,publish_route


def render_strategy_comparison(ui,scope,entries,status,period_end, *, observed_entries=None):
    ui.subheader('Strategy Comparison')
    observed_mode = False
    if observed_entries is not None:
        basis = ui.radio('Classification basis', ['Original entry strategy', 'First verified observed strategy'],
                         index=1, horizontal=True, key='campaigniq_strategy_classification_basis')
        observed_mode = basis == 'First verified observed strategy'
    heading = 'Observed strategy' if observed_mode else 'Entry strategy'
    basis_description = 'First verified observed position or opening legs; original entry may be unknown.' if observed_mode else 'Strategy at campaign entry.'
    ui.caption(f'{basis_description}  equity/options only. Realized closes within the selected reporting range. Position status is as of {period_end:%b %d, %Y}. Subsequent rolls and assignments can change the strategy. These are dollar outcomes, not returns on capital or risk-adjusted rankings.')
    if not scope.campaigns:
        ui.info('No eligible realized campaigns are available for strategy comparison.');return
    groups,details=compare_strategies(scope.campaigns,entries,status, observed_evidence=observed_entries if observed_mode else None)
    rows=[{heading:r['strategy'],'Campaigns':r['campaigns'],'Closed':r['closed'],'Open':r['open'],
           'Status unknown':r['unknown'],'Closed win rate':float(r['win_rate']*100) if r['win_rate'] is not None else None,
           'Realized P&L':float(r['realized']),'Average realized P&L':float(r['average']),'Median realized P&L':float(r['median'])} for r in groups]
    frame=pd.DataFrame(rows).sort_values('Realized P&L',ascending=False).reset_index(drop=True)
    styled=frame.style.format({'Realized P&L':display_money,'Average realized P&L':display_money,
                              'Median realized P&L':display_money,'Closed win rate':'{:.1f}%'},na_rep='Unavailable')
    render_dataframe(ui,styled,hide_index=True,use_container_width=True)
    ui.caption('Win rate is the fraction of confirmed closed campaigns with positive realized P&L in the selected range; breakevens stay in the denominator. It is not a lifetime win rate when the range omits earlier closes. Totals, averages, and medians include all eligible campaigns with realized closes, including open and status-unknown campaigns. Campaigns without any selected-period realized closes are absent.')
    ui.caption('Short put does not establish cash collateral. Covered call requires a matching stock purchase in the opening order or verified prior stock coverage after allowing for existing short calls. Stock entry describes an initial stock order; Stock position describes observed holdings. Neither promises that the campaign remained stock-only. Unclassified campaigns remain visible.')
    if observed_mode:
        ui.caption('Observed groups describe the earliest verified evidence retained for each campaign, even if it predates the selected reporting range. They do not attribute all campaign profit to that one strategy, prove original intent, or estimate missing premiums. No later strategy is substituted merely to obtain a supported label.')
    review_groups, review_rows = unclassified_review(details)
    if review_rows:
        ui.markdown('##### Why campaigns are Unclassified')
        render_money_table(ui, [dict(r, **{'Realized P&L': float(r['Realized P&L'])}) for r in review_groups], ('Realized P&L',))
        ui.caption('These counts use the actual explanations for the selected campaigns. The review does not change classifications or broker results.')
        review = pd.DataFrame(review_rows)
        review['Realized P&L'] = review['Realized P&L'].map(lambda value: format(value, '.2f'))
        ui.download_button('Download Unclassified campaign review', review.to_csv(index=False).encode('utf-8'),
                           file_name=f"campaigniq_unclassified_{'observed' if observed_mode else 'entry'}_review_through_{period_end:%Y-%m}.csv",
                           mime='text/csv', key=f'campaigniq_unclassified_review_download_{period_end}')
    ui.markdown('##### Campaign classifications')
    details=tuple(sorted(details,key=lambda r:(r['strategy'],-r['campaign'].realized_pnl,r['campaign'].campaign_id)))
    rows=[{heading:r['strategy'],'Underlying':r['campaign'].underlying,'Campaign':r['campaign'].campaign_id,
           'Realized P&L':float(r['campaign'].realized_pnl),'Position status':'Open' if r['open'] is True else 'Closed' if r['open'] is False else 'Unavailable',
           'Classification basis':r['basis'], 'Observation date':r['observed_at'].date() if r['observed_at'] else None,
           'Classification evidence':r['evidence']} for r in details]
    table=pd.DataFrame(rows).style.format({'Realized P&L':display_money})
    fingerprint=hashlib.sha256(json.dumps([(r['campaign'].drilldown_id,r['strategy'],str(r['campaign'].realized_pnl)) for r in details]).encode()).hexdigest()[:16]
    key=selection_table_key(ui,f'campaigniq_strategy_rows_{period_end}_{observed_mode}_{fingerprint}')
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
