"""Strict statement evidence for a month without trading, with separate cash income."""
from datetime import date
from decimal import Decimal
import re

MONEY=r'\(?-?\$?\d[\d,]*\.\d{2}\)?'
def money(value):
    negative=value.startswith('(')
    value=value.strip('()').replace('$','').replace(',','')
    return -Decimal(value) if negative else Decimal(value)

def analyze(text, *, start, end):
    """Only zero trading and fully itemized dividend/interest cash income qualify."""
    if text.count('Transactions - Summary')!=1 or text.count('Transaction Details')!=1:
        raise ValueError('One complete statement transaction summary and detail section required')
    summary=text.split('Transactions - Summary',1)[1].split('Transaction Details',1)[0]
    # Reject offsetting trades, transfers, corporate actions and expenses even
    # when unchanged closing quantities could conceal them.
    amounts=[money(v) for v in re.findall(MONEY,summary.split('Other Activity',1)[0])]
    if len(amounts)!=8:raise ValueError('Complete eight-column cash summary required')
    opening,deposits,withdrawals,purchases,sales,income,expenses,ending=amounts
    if any(v!=0 for v in (deposits,withdrawals,purchases,sales,expenses)):
        raise ValueError('Statement contains trading, transfers or expenses; use the normal monthly workflow')
    if re.search(r'Other Activity\s+\$0\.00\b',summary) is None:
        raise ValueError('Non-cash statement activity must be explicitly zero')
    gains=text.split('Gain or (Loss) Summary',1)
    if len(gains)!=2:raise ValueError('Statement realized gain/loss summary required')
    match=re.search(r'This\s+('+MONEY+r')\s+('+MONEY+r')\s+('+MONEY+r')\s+('+MONEY+r')\s+('+MONEY+r')\s+('+MONEY+r')\s+Period',gains[1])
    if match is None or any(money(v)!=0 for v in match.groups()):
        raise ValueError('Statement must explicitly report zero realized gains and losses')
    details=text.split('Transaction Details',1)[1]
    if 'Total Transactions' not in details:raise ValueError('Complete transaction detail total required')
    details,tail=details.split('Total Transactions',1)
    total=re.match(r'\s*('+MONEY+r')',tail)
    if total is None or money(total[1])!=income:raise ValueError('Transaction detail total differs from cash income')
    rows=[]
    for line in details.splitlines():
        line=line.strip()
        if not line:continue
        if line.startswith(('Symbol/','Date ','CUSIP ')):continue
        match=re.fullmatch(r'(\d{2}/\d{2})\s+(Dividend\s+(?:Qual\. Dividend|Cash Dividend)|Interest\s+Credit Interest)\s+(\S+)\s+(.+?)\s+('+MONEY+r')',line)
        if match is None:raise ValueError('Unrecognized statement transaction detail; no-trading confirmation withheld')
        at=date(start.year,*map(int,match[1].split('/')))
        if not start<=at<=end:raise ValueError('Cash income date is outside the selected month')
        amount=money(match[5])
        if amount<=0:raise ValueError('Only positive itemized dividend or interest income is supported')
        rows.append(dict(date=str(at),kind='dividend' if match[2].startswith('Dividend') else 'interest',symbol=match[3],description=match[4],amount_usd=str(amount)))
    if sum((Decimal(r['amount_usd']) for r in rows),Decimal(0))!=income or opening+income!=ending:
        raise ValueError('Statement cash income does not reconcile exactly')
    return dict(opening_cash_usd=str(opening),income_usd=str(income),ending_cash_usd=str(ending),
        realized_trading_pnl_usd='0.00',cash_income=rows)
