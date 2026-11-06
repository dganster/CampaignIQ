"""Synthetic income/expense controls, including internal trades and external cash."""
from decimal import Decimal

def controls(*,year=2025,month=1,dividends='59.00',interest='0.25',charged='-2.00',fees='-0.50',deposits='200.00',withdrawals='-50.00',purchases='-1000.00',sales='1500.00',other='0.00'):
    from calendar import monthrange
    from datetime import date
    start=date(year,month,1);end=start.replace(day=monthrange(year,month)[1]);D=Decimal
    income=D(dividends)+D(interest);expense=D(charged)+D(fees);opening=D('1000.00')
    ending=opening+sum(map(D,(deposits,withdrawals,purchases,sales)))+income+expense
    def cash(value):return '('+f'${abs(value):,.2f}'+')' if value<0 else f'${value:,.2f}'
    text=(f'Ending Account Value as of {end:%m/%d} Beginning Account Value as of {start:%m/%d}\n$11,000.00 $10,000.00\n'
        'Total Income $0.00 '+cash(income)+' $0.00 '+cash(income)+'\nTransactions - Summary\n'
        'Beginning Cash Deposits Withdrawals Purchases Sales/Redemptions Dividends/Interest Expenses Ending Cash\n'+
        ' '.join(cash(v) for v in (opening,D(deposits),D(withdrawals),D(purchases),D(sales),income,expense,ending))+'\nOther Activity '+cash(D(other))+'\n'
        'Transaction Details\nDate Category Action Symbol Description Amount\n')
    for category,action,symbol,description,value in (
        ('Dividend','Qual. Dividend','SPY','SYNTHETIC ETF',D(dividends)),('Interest','Credit Interest','SCHWAB1INT','SYNTHETIC CASH',D(interest)),
        ('Expense','Margin Interest','','SYNTHETIC LOAN',D(charged)),('Expense','Service Fee','','SYNTHETIC FEE',D(fees))):
        if value:text+=f'{start:%m}/20 {category} {action} {symbol} {description} '+cash(value)+'\n'
    text+='Total Transactions '+cash(ending-opening)+'\n'
    return text
