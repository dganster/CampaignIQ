"""Display verified option rolls without treating a credit as realized profit."""
from decimal import Decimal
from campaigniq.analytics.roll_analysis import leg_quantity, leg_cash_flow
from campaigniq.ui.financial_format import display_money, render_money_table


def _decimal(value):
    text=format(value,'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def contract_label(contract):
    return f'{contract.underlying} {contract.expiration:%b %d %Y} ${_decimal(contract.strike)} {contract.option_type.value.title()}'


def render_roll_analysis(ui, rolls, warnings):
    ui.markdown('#### Roll analysis')
    ui.caption('Complete option roll orders linked to this campaign through verified archived exports and retained journal legs, across published months. Each row covers the whole order, including any legs linked to other campaigns; order cash flows are not allocated campaign P&L.')
    if not rolls:
        ui.info('No complete verified option rolls are available for this campaign.')
    else:
        a,b,c=ui.columns(3)
        a.metric('Verified roll orders',len(rolls))
        credits=sum((r.net_cash_flow for r in rolls if r.net_cash_flow>0),Decimal('0'))
        debits=sum((-r.net_cash_flow for r in rolls if r.net_cash_flow<0),Decimal('0'))
        b.metric('Gross roll credits',display_money(credits))
        c.metric('Gross roll debits paid',display_money(debits))
        rows=[];running=Decimal('0')
        for number,roll in enumerate(rolls,1):
            running+=roll.net_cash_flow
            rows.append({'Roll':number,'Date':roll.occurred_at.date(),'Source time':roll.occurred_at.time(),
                         'Closed contracts':' + '.join(contract_label(l.instrument) for l in roll.close_legs),
                         'Opened contracts':' + '.join(contract_label(l.instrument) for l in roll.open_legs),
                         'Credit / Debit':'Credit' if roll.net_cash_flow>0 else 'Debit' if roll.net_cash_flow<0 else 'Even',
                         'Net before fees':float(roll.net_cash_flow),'Cumulative before fees':float(running),
                         'Strike change':('+' if roll.strike_change>0 else '-' if roll.strike_change<0 else '')+'$'+_decimal(abs(roll.strike_change)) if roll.strike_change is not None else 'Multiple legs / type change',
                         'Expiration shift':f'{roll.expiration_change_days:+d} days' if roll.expiration_change_days is not None else 'Multiple legs / type change',
                         'Contract quantity change':f'{roll.contract_quantity_change:+f}' if roll.contract_quantity_change is not None else 'Multiple legs / type change'})
        render_money_table(ui,rows,('Net before fees','Cumulative before fees'))
        for number,roll in enumerate(rolls,1):
            with ui.expander(f'Roll {number} · {roll.occurred_at:%b %d, %Y} · leg details'):
                legs=[]
                for phase,items in (('Close',roll.close_legs),('Open',roll.open_legs)):
                    for leg in items:
                        quantity=leg_quantity(leg)
                        price=sum((abs(e.quantity)*e.execution_price for e in leg.executions),Decimal('0'))/quantity
                        legs.append({'Phase':phase,'Buy / Sell':leg.side.value.title(),'Contract':contract_label(leg.instrument),
                                     'Contracts':float(quantity),'Fills':len(leg.executions),'Average execution price':float(price),
                                     'Gross cash flow':float(leg_cash_flow(leg))})
                render_money_table(ui,legs,('Average execution price','Gross cash flow'))
    ui.caption('Credits and debits are execution cash flows before fees, not realized P&L or evidence that a roll improved the trade. Cumulative values include only the verified rolls shown. Separate roll fees are unavailable.')
    for warning in warnings:
        ui.warning(warning)
