"""Statement-bound cash income, expenses and account-value bridge, without tax inference."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re

NUMBER=r'(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}'
MONEY=rf'(?:\(\$?{NUMBER}\)|-?\$?{NUMBER})'
TOKEN=re.compile(rf'(?<![\d,.]){MONEY}(?![\d,.])')
def amount(raw):
    if re.fullmatch(MONEY,raw) is None:raise ValueError('Invalid statement monetary amount')
    negative=raw.startswith('(');value=Decimal(raw.strip('()').replace('$','').replace(',',''))
    return -value if negative else value

@dataclass(frozen=True)
class IncomeItem:
    date:str
    category:str
    action:str
    symbol:str|None
    description:str
    amount_usd:Decimal
    ordinal:int
    campaign_id:str|None=None
    attribution_status:str='Account-level cash; entitlement date not supplied'

@dataclass(frozen=True)
class StatementIncome:
    period_start:date
    period_end:date
    dividends:Decimal
    credited_interest:Decimal
    interest_expense:Decimal
    other_expense:Decimal
    opening_cash:Decimal
    ending_cash:Decimal
    deposits:Decimal
    withdrawals:Decimal
    purchases:Decimal
    sales:Decimal
    other_activity:Decimal
    items:tuple[IncomeItem,...]
    beginning_value:Decimal|None=None
    ending_value:Decimal|None=None

    @property
    def net_income(self):return self.dividends+self.credited_interest+self.interest_expense+self.other_expense
    @property
    def account_investment_result(self):
        if self.beginning_value is None or self.ending_value is None or self.other_activity!=0:return None
        return self.ending_value-self.beginning_value-self.deposits-self.withdrawals
    @property
    def simple_return(self):
        if self.account_investment_result is None or self.deposits or self.withdrawals or self.beginning_value<=0:return None
        return self.account_investment_result/self.beginning_value

DIVIDEND=r'(Qual\. Dividend|Cash Dividend|Non-Qual Dividend|Non-Qual Div|Non-Qualified Div|Reinvest Dividend|Special Dividend)'
INTEREST=r'(Credit Interest|Cash Interest)'
EXPENSE=r'(Margin Interest|Interest Charged|Advisory Fee|Service Fee|Foreign Tax Paid|Tax Withholding)'

def parse_statement_income(text,*,period_start,period_end):
    from calendar import monthrange
    if period_start.day!=1 or period_end!=date(period_start.year,period_start.month,monthrange(period_start.year,period_start.month)[1]):
        raise ValueError('Complete calendar month required for statement income')
    label=f'{period_start:%B} 1-{period_end.day}, {period_start.year}'
    if label not in text:raise ValueError('Statement does not declare the selected calendar period')
    if text.count('Transactions - Summary')!=1 or len(re.findall(r'(?m)^\s*Transaction Details\s*$',text))!=1:
        raise ValueError('Complete statement cash summary and transaction details required')
    # The account-bound caller also validates the statement's calendar period.
    summary=text.split('Transactions - Summary',1)[1].split('Transaction Details',1)[0]
    if 'Other Activity' not in summary:raise ValueError('Explicit non-cash activity control required')
    raw,other=summary.split('Other Activity',1)
    values=[amount(v.group()) for v in TOKEN.finditer(raw)]
    if len(values)!=8:raise ValueError('Complete eight-column statement cash summary required')
    opening,deposits,withdrawals,purchases,sales,income,expenses,ending=values
    if deposits<0 or withdrawals>0 or purchases>0 or sales<0 or expenses>0:
        raise ValueError('Statement cash summary sign convention is unsupported')
    if opening+deposits+withdrawals+purchases+sales+income+expenses!=ending:
        raise ValueError('Statement cash summary does not reconcile exactly')
    match=re.match(r'\s*('+MONEY+r')',other)
    if match is None:raise ValueError('Missing non-cash activity control')
    other_value=amount(match[1])
    details=text.split('Transaction Details',1)[1]
    if details.count('Total Transactions')!=1:raise ValueError('Complete statement transaction detail total required')
    details,tail=details.split('Total Transactions',1)
    total=re.match(r'\s*('+MONEY+r')',tail)
    if total is None or amount(total[1])!=deposits+withdrawals+purchases+sales+income+expenses:
        raise ValueError('Statement transaction total differs from cash summary')
    items=[];current=None
    for ordinal,line in enumerate(details.splitlines()):
        line=line.strip()
        leading=re.match(r'^(\d{2}/\d{2})\s+',line)
        if leading:
            current=date(period_start.year,*map(int,leading[1].split('/')))
            if not period_start<=current<=period_end:raise ValueError('Transaction date outside selected statement period')
            line=line[leading.end():]
        category=re.match(r'^(Dividend|Interest|Expense)\s+',line)
        if category is None:continue
        if current is None:raise ValueError('Cash income or expense lacks an explicit transaction date')
        rest=line[category.end():]
        last=re.search(r'('+MONEY+r')\s*$',rest)
        if last is None:raise ValueError('Income/expense detail has no exact monetary amount')
        value=amount(last[1]);body=rest[:last.start()].strip();kind=category[1]
        if kind=='Dividend':
            action=re.fullmatch(DIVIDEND+r'\s+(\S+)\s+(.+)',body)
            if action is None or re.search(r'(?i)return of capital|capital gain|payment in lieu|withhold',body):
                raise ValueError('Unclassified distribution; no income or campaign attribution inferred')
            label,symbol,description=action.groups();bucket='dividend'
        elif kind=='Interest':
            action=re.fullmatch(INTEREST+r'\s+(\S+)\s+(.+)',body)
            if action is None:raise ValueError('Unsupported statement interest detail')
            label,symbol,description=action.groups();bucket='credited_interest'
        else:
            action=re.fullmatch(EXPENSE+r'(?:\s+(.+))?',body)
            if action is None or value>0:raise ValueError('Unsupported statement expense or expense refund')
            label,description=action.groups();symbol=None;description=description or label
            if re.search(r'(?i)\bforex\b|\bfutures\b|\bcrypto\b',description):raise ValueError('Cross-market expense needs independent classification')
            bucket='interest_expense' if label in ('Margin Interest','Interest Charged') else 'other_expense'
        items.append(IncomeItem(str(current),bucket,label,symbol,description,value,ordinal))
    def summed(bucket):return sum((r.amount_usd for r in items if r.category==bucket),Decimal(0))
    dividends,credited,charged,other_expense=(summed(k) for k in ('dividend','credited_interest','interest_expense','other_expense'))
    if dividends+credited!=income or charged+other_expense!=expenses:
        raise ValueError('Itemized cash income or expenses differ from statement summary')
    # Where available, the separate income summary is an additional control.
    totals=re.findall(r'Total Income\s+('+MONEY+r')\s+('+MONEY+r')\s+('+MONEY+r')\s+('+MONEY+r')',text)
    if len(totals)>1:raise ValueError('Ambiguous statement income summary')
    if totals and amount(totals[0][0])+amount(totals[0][1])!=income:
        raise ValueError('Statement income summary differs from transaction income')
    beginning=ending_value=None
    pattern=r'Ending Account Value as of (\d{2}/\d{2})\s+Beginning Account Value as of (\d{2}/\d{2})\s+('+MONEY+r')\s+('+MONEY+r')'
    bridge=re.findall(pattern,text)
    if len(bridge)>1:raise ValueError('Ambiguous account valuation controls')
    if bridge:
        ending_date,beginning_date,last,first=bridge[0]
        if ending_date!=period_end.strftime('%m/%d') or beginning_date!=period_start.strftime('%m/%d'):
            raise ValueError('Statement account valuation dates differ from selected period')
        beginning,ending_value=amount(first),amount(last)
    return StatementIncome(period_start,period_end,dividends,credited,charged,other_expense,opening,ending,
        deposits,withdrawals,purchases,sales,other_value,tuple(items),beginning,ending_value)
