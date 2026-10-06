"""Assignment delivery outcomes, separate from total campaign economics."""
import pandas as pd
from campaigniq.ui.roll_analysis_view import contract_label
from campaigniq.ui.financial_format import render_money_table


def render_assignment_outcomes(ui, outcomes):
    ui.markdown('#### Assignment outcomes')
    ui.caption('Short standard stock-option assignments linked to this campaign, across retained published history. Long-option exercises and futures options are excluded. Dates are journal posting dates.')
    if not outcomes:
        ui.info('No retained short stock-option assignments are linked to this campaign.')
        return
    rows = []
    for outcome in outcomes:
        acquired = outcome['shares'] > 0
        remaining = outcome['remaining']
        status = ('Delivery or later history needs review' if not outcome['linked'] else
                  'Shares called away' if not acquired else
                  'Acquired shares subsequently closed' if remaining == 0 else
                  'Acquired shares partly closed' if outcome['closed_shares'] else
                  'Acquired shares remain open')
        rows.append({'Assignment date': outcome['date'], 'Contract': contract_label(outcome['contract']),
                     'Contracts': int(outcome['contracts']), 'Outcome': status,
                     'Share change': int(outcome['shares']),
                     'Cash at strike': float(outcome['strike_cash']) if outcome['linked'] else None,
                     'Acquired shares still open': int(remaining) if remaining is not None else None,
                     'Linked stock realized P&L': float(outcome['realized']) if outcome['realized'] is not None else None,
                     'Linked broker closes': outcome['broker_records']})
    frame = pd.DataFrame(rows)
    for column in ('Contracts', 'Share change', 'Acquired shares still open', 'Linked broker closes'):
        frame[column] = frame[column].astype('Int64')
    render_money_table(ui, frame, ('Cash at strike', 'Linked stock realized P&L'))
    ui.caption('Cash at strike is the contractual stock payment or receipt before premiums and fees; it is not adjusted cost basis or profit. Linked stock realized P&L uses only broker closes matched to the affected lots and may include assignment premiums already embedded by the broker. It can be partial when broker records or history are missing. No separate premium is added.')
    ui.caption('Remaining shares refer to the latest retained evidence, including months outside the selected range. Unavailable means the evidence does not establish the value. Subsequent share purchases are not treated as replacement of called-away shares.')
