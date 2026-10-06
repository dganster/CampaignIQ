"""Campaign economics with broker facts separated from price-derived cash flow."""
from campaigniq.analytics.campaign_economics import summarize_campaign_economics
from campaigniq.ui.table_layout import render_dataframe


def render_campaign_economics(ui, *, selected_records, campaign_records, campaign_entries, lifetime_scope, reporting_month):
    selected = summarize_campaign_economics(selected_records)
    campaign = summarize_campaign_economics(campaign_records, campaign_entries)
    ui.markdown('#### Campaign economics')
    if lifetime_scope:
        ui.caption('All published realized closes linked to this campaign, including closes outside the selected reporting range. Dividends, financing, and unrealized changes are not included.')
    else:
        ui.caption(f'Realized closes for {reporting_month}. This older campaign ID cannot safely link broker results across months.')
    a, b, c = ui.columns(3)
    a.metric('Campaign realized P&L' if lifetime_scope else 'Reported realized P&L', f'${campaign.total_realized:,.2f}')
    b.metric('Stock realized P&L', f'${campaign.stock_realized:,.2f}')
    c.metric('Options realized P&L', f'${campaign.option_realized:,.2f}')
    ui.caption(f'The Campaign Detail summary covers {reporting_month}: ${selected.total_realized:,.2f}. This economics view uses the scope stated here.')
    rows = [dict(Component='Stock', **{'Realized P&L':float(campaign.stock_realized)}),
            dict(Component='Options', **{'Realized P&L':float(campaign.option_realized)})]
    if campaign.other_realized:
        rows.append(dict(Component='Other', **{'Realized P&L':float(campaign.other_realized)}))
    rows.append(dict(Component='Total', **{'Realized P&L':float(campaign.total_realized)}))
    render_dataframe(ui, rows, hide_index=True, use_container_width=True,
                     column_config={'Realized P&L':ui.column_config.NumberColumn('Realized P&L',format='$%0,.2f')})
    if not campaign.reconciled:
        ui.warning('Some broker basis or gain/loss checks need review; these are reported results, not fully reconciled economics.')
    ui.caption('These components use broker-reported realized results. Assignment-related option premiums may be included in stock proceeds or basis. Fees reflected in broker results are already included; do not subtract them again.')
    if campaign.record_count:
        with ui.expander('How the reported result adds up'):
            render_dataframe(ui, [
                {'Component':'Reported proceeds','Amount':float(campaign.reported_proceeds)},
                {'Component':'Less reported cost basis','Amount':-float(campaign.reported_basis)},
                {'Component':'Reported loss adjustments','Amount':float(campaign.reported_adjustments)},
                {'Component':'Reported realized P&L','Amount':float(campaign.total_realized)}],
                hide_index=True,use_container_width=True,
                column_config={'Amount':ui.column_config.NumberColumn('Amount',format='$%0,.2f')})
    ui.markdown('##### Observed option trading cash flows')
    ui.caption('Retained trades linked to this campaign, across months. Gross execution price × quantity × 100 for standard stock options, before fees. These cash flows are not added to realized P&L and are not the profit on an open position.')
    if campaign.observed_option_trade_count:
        cash_rows=[{'Component':'Short-option premiums received','Amount':float(campaign.short_premiums_received)},
                   {'Component':'Short-option buybacks paid','Amount':-float(campaign.short_option_buybacks)},
                   {'Component':'Long-option purchases paid','Amount':-float(campaign.long_option_purchases)},
                   {'Component':'Long-option sale receipts','Amount':float(campaign.long_option_sale_receipts)},
                   {'Component':'Net observed option cash flow','Amount':float(campaign.observed_option_cash_flow)}]
        render_dataframe(ui,cash_rows,hide_index=True,use_container_width=True,
                         column_config={'Amount':ui.column_config.NumberColumn('Amount',format='$%0,.2f')})
    else:
        ui.info('No priced option trades are available in the retained campaign evidence.')
    ui.caption('Separate trading fees: unavailable from the retained position journal. No fee amount is estimated.')
    if campaign.carried_position_count or campaign.missing_option_trade_prices:
        ui.warning('The cash-flow view may be incomplete: carried positions can lack original premiums, and unpriced trades are omitted. It is not a complete campaign cash ledger.')
