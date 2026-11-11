"""Strict optional cash-only Thinkorswim export check; no trading inference from size."""
from collections import Counter
import csv
from datetime import datetime
from decimal import Decimal
import re
from campaigniq.broker_account_binding import verify_document


def check_history(raw,known,start,end,cash):
    text=raw.decode('utf-8-sig');verify_document(known,'thinkorswim_trade_history',text)
    if not text.splitlines():raise ValueError('Supporting Trade History is empty; remove this optional upload')
    first=text.splitlines()[0]
    period=re.fullmatch(r'Account Statement for .+ since (\d{1,2}/\d{1,2}/\d{2,4}) through (\d{1,2}/\d{1,2}/\d{2,4})',first.strip())
    def day(s):return datetime.strptime(s,'%m/%d/%Y' if len(s.split('/')[-1])==4 else '%m/%d/%y').date()
    if period is None or (day(period[1]),day(period[2]))!=(start,end):raise ValueError('Supporting Trade History must declare this exact month')
    lines=[line.strip() for line in text.splitlines()];sections={};current=None
    allowed={'Cash Balance','Account Order History','Account Trade History','Forex Statements','Equities','Options','Profits and Losses','Account Summary'}
    for i,line in enumerate(lines[1:],1):
        if not line:continue
        if ',' not in line and line in allowed:
            if line in sections:raise ValueError('Duplicate supporting export section')
            current=line;sections[current]=[];continue
        if current is None:raise ValueError('Unsupported supporting export layout; remove this file or use the trading workflow')
        if ',' not in line:raise ValueError('Unrecognized supporting export section or row')
        sections[current].append(next(csv.reader([line])))
    if not {'Cash Balance','Account Trade History'}<=set(sections):raise ValueError('Supporting export must include explicit cash and trade-history sections')
    trade=sections['Account Trade History']
    if not trade or 'Exec Time' not in trade[0] or 'Symbol' not in trade[0] or 'Qty' not in trade[0]:raise ValueError('Unsupported trade-history columns')
    if any(any(cell.strip() for cell in row) for row in trade[1:]):raise ValueError('Trade History contains execution rows. Choose I traded or remove this supporting export.')
    orders=sections.get('Account Order History',[])
    if any(any(cell.strip() for cell in row) for row in orders[1:]):raise ValueError('Order activity needs the normal trading workflow or explicit source removal')
    def money(value):
        value=value.strip()
        if not value:return Decimal(0)
        if not re.fullmatch(r'(?:-?\$?(?:\d+|\d{1,3}(?:,\d{3})+)\.\d{2}|\(\$?(?:\d+|\d{1,3}(?:,\d{3})+)\.\d{2}\))',value):raise ValueError('Unsupported supporting cash amount')
        return Decimal(value.strip('()').replace('$','').replace(',',''))*(-1 if value.startswith('(') else 1)
    rows=sections['Cash Balance']
    if not rows or not {'DATE','TIME','TYPE','AMOUNT','BALANCE','Misc Fees','Commissions & Fees'}<=set(rows[0]):raise ValueError('Complete supporting cash columns required')
    header=rows[0];running=Decimal(cash['opening_cash_usd']);observed=[];previous=None
    if len(rows)<2:raise ValueError('Supporting cash balances required')
    for index,row in enumerate(rows[1:]):
        if len(row)!=len(header):raise ValueError('Malformed supporting cash row')
        get=lambda key:row[header.index(key)].strip()
        at=day(get('DATE'))
        when=datetime.combine(at,datetime.strptime(get('TIME'),'%H:%M:%S').time())
        if not start<=at<=end or (previous is not None and when<previous):raise ValueError('Supporting cash dates are outside this month or out of order')
        previous=when;kind=get('TYPE');amount=money(get('AMOUNT'))
        if money(get('Misc Fees')) or money(get('Commissions & Fees')):raise ValueError('Supporting export fees require the normal trading workflow')
        if index==0 and (kind!='BAL' or at!=start):raise ValueError('Supporting cash export must start with the month opening balance')
        if kind=='BAL':
            if amount:raise ValueError('Balance rows cannot introduce cash activity')
        elif kind in ('DIV','INT') and amount>0:
            running+=amount;observed.append((str(at),'dividend' if kind=='DIV' else 'interest',amount))
        else:raise ValueError('Supporting export has trading, transfers or unsupported cash activity; use the normal workflow or remove it')
        if money(get('BALANCE'))!=running:raise ValueError('Supporting running balance does not reconcile with statement income')
    expected=[(r['date'],r['kind'],Decimal(r['amount_usd'])) for r in cash['cash_income']]
    if Counter(observed)!=Counter(expected) or running!=Decimal(cash['ending_cash_usd']) or previous.date()!=end:raise ValueError('Supporting cash activity or closing coverage differs from statement')
    forex=sections.get('Forex Statements',[])
    if forex:
        fh=forex[0]
        if not {'TYPE','AMOUNT','BALANCE'}<=set(fh):raise ValueError('Unsupported Forex cash columns')
        for row in forex[1:]:
            if len(row)!=len(fh) or row[fh.index('TYPE')].strip()!='BAL' or money(row[fh.index('AMOUNT')]) or money(row[fh.index('BALANCE')]):raise ValueError('Forex cash activity requires separate monthly controls')
    return dict(no_execution_rows=True,cash_balances_and_income_verified=True)
