"""Show reliance on winning campaigns without dividing by small net results."""
import hashlib
import json
import pandas as pd
from campaigniq.analytics.profit_concentration import underlying_contributions
from campaigniq.ui.financial_format import display_money, render_money_table
from campaigniq.ui.table_layout import render_dataframe, selection_table_key
from campaigniq.ui.analytics_navigation import remember_return, publish_route


def _campaign_table(ui, campaigns, gross, *, label):
    if not campaigns:
        ui.info(f'No {label} campaigns in this scope.')
        return
    campaigns = campaigns[:10]
    rows = [{'Rank': rank, 'Underlying': r.underlying, 'Campaign': r.campaign_id,
             'Realized P&L': float(r.realized_pnl),
             'Share of gross '+label: float(abs(r.realized_pnl)/gross*100)}
            for rank,r in enumerate(campaigns,1)]
    frame = pd.DataFrame(rows)
    table = frame.style.format({'Realized P&L':display_money,'Share of gross '+label:'{:.1f}%'})
    fingerprint = hashlib.sha256(json.dumps([r.drilldown_id for r in campaigns]).encode()).hexdigest()[:16]
    key = selection_table_key(ui,f'campaigniq_concentration_{label}_{fingerprint}')
    def selected():
        indices=ui.session_state.get(key,{}).get('selection',{}).get('rows',[])
        if not indices or not isinstance(indices[0],int) or not 0<=indices[0]<len(campaigns):
            return
        campaign=campaigns[indices[0]]
        if hasattr(ui,'query_params'):
            remember_return(ui)
        ui.session_state.update(campaigniq_primary_view='Campaigns',campaigniq_campaign_month='All months',
                                campaigniq_campaign_symbol=campaign.underlying,campaigniq_campaign_result='All results',
                                campaigniq_campaign_detail=campaign.drilldown_id)
        if hasattr(ui,'query_params'):
            publish_route(ui)
    render_dataframe(ui,table,hide_index=True,use_container_width=True,key=key,
                     on_select=selected,selection_mode='single-row')


def render_profit_concentration(ui, summary):
    ui.subheader('Profit Concentration')
    ui.caption('Selected reporting months only. Reconciled, unambiguous equity/options realized closes; FOREX, crypto, futures, unrealized changes, dividends, and financing are excluded. Immutable campaign IDs combine across months; older local IDs remain separate by month.')
    if not summary.campaigns:
        ui.info('No eligible realized campaigns are available for this reporting range.')
    else:
        a,b,c=ui.columns(3)
        a.metric('Gross winning campaign P&L',display_money(summary.gross_profits))
        b.metric('Gross losing campaign P&L',display_money(-summary.gross_losses))
        c.metric('Included net realized P&L',display_money(summary.net_realized))
        rows=[]
        for count in (1,5,10):
            actual,pnl,share,remainder=summary.top(count)
            rows.append({'Winning campaigns':f'Top {count}', 'Available winners included':actual,
                         'Realized P&L':float(pnl),'Share of gross profits':float(share*100) if share is not None else None,
                         'Net without these winners':float(remainder) if actual else None})
        frame=pd.DataFrame(rows)
        styled=frame.style.format({'Realized P&L':display_money,'Net without these winners':display_money,
                                   'Share of gross profits':'{:.1f}%'},na_rep='Not applicable')
        render_dataframe(ui,styled,hide_index=True,use_container_width=True)
        ui.caption('Profit shares divide by gross winning campaign P&L, not net P&L. Removing winners is an arithmetic comparison, not a forecast or a simulation of a different trading strategy. When fewer than 5 or 10 winners exist, all available winners are used.')
        ui.markdown('##### Largest winning campaigns')
        _campaign_table(ui,summary.winners,summary.gross_profits,label='profits')
        ui.markdown('##### Largest losing campaigns')
        _campaign_table(ui,summary.losers,summary.gross_losses,label='losses')
        ui.caption('Click a campaign row to inspect its details. A campaign spanning months opens its latest included reporting-month detail; the concentration amount is its combined result within the selected range.')
        ui.markdown('##### Contributions by underlying')
        rows=[{'Underlying':r['underlying'],'Campaigns':r['campaigns'],
               'Gross winning P&L':float(r['profits']),'Gross losing P&L':-float(r['losses']),
               'Net realized P&L':float(r['net'])} for r in underlying_contributions(summary)]
        render_money_table(ui,rows,('Gross winning P&L','Gross losing P&L','Net realized P&L'))
        ui.caption('Gross profits and losses classify campaigns after combining their selected-period closes, so a campaign’s winning and losing closes offset before classification. Each campaign contributes to one underlying.')
    if summary.excluded_records:
        ui.warning(f'{summary.excluded_records} broker record(s), net {display_money(summary.excluded_pnl)}, are excluded because attribution, reconciliation, or instrument scope does not qualify. Included net P&L can differ from the Overview total.')
