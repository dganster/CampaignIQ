from datetime import date
from decimal import Decimal as D
import pytest
from campaigniq.statement_income import parse_statement_income,amount
from campaigniq.income_rehearsal_sources import controls

START,END=date(2025,1,1),date(2025,1,31)
def text(**kwargs):return 'January 1-31, 2025\n'+controls(**kwargs)
def parse(value):return parse_statement_income(value,period_start=START,period_end=END)

def test_active_month_includes_dividends_interest_and_costs_but_not_trade_cash_or_transfers():
    result=parse(text())
    assert (result.dividends,result.credited_interest,result.interest_expense,result.other_expense)==(D(59),D('.25'),D(-2),D('-.50'))
    assert result.net_income==D('56.75') and result.ending_cash==D('1706.75')
    assert result.account_investment_result==850 and result.simple_return is None
    assert len(result.items)==4 and all(r.campaign_id is None for r in result.items)

def test_no_external_flows_rate_includes_income_once_and_change_in_market_value():
    result=parse(text(deposits='0.00',withdrawals='0.00'))
    assert result.account_investment_result==1000 and result.simple_return==D('.10')
    assert result.account_investment_result!=1000+result.net_income

@pytest.mark.parametrize('raw',['1,00.00','NaN','Infinity','1.000','(−1.00)','(-1.00)','1,000,00'])
def test_malformed_money_is_not_silently_accepted(raw):
    with pytest.raises(ValueError):amount(raw)

@pytest.mark.parametrize('change',[
    lambda s:s.replace('$1,706.75','$1,706.76'),
    lambda s:s.replace('SYNTHETIC ETF $59.00','SYNTHETIC ETF $58.00'),
    lambda s:s.replace('Total Transactions $706.75','Total Transactions $706.76'),
    lambda s:s.replace('Qual. Dividend','Return of Capital'),
    lambda s:s.replace('Qual. Dividend','Capital Gain Distribution'),
    lambda s:s.replace('Qual. Dividend','Payment in Lieu'),
    lambda s:s.replace('Margin Interest','Unknown Charge'),
    lambda s:s.replace('01/20 Dividend','02/20 Dividend'),
    lambda s:s.replace('January 1-31, 2025','February 1-28, 2025'),
    lambda s:s.replace('Total Income $0.00 $59.25','Total Income $0.00 $60.25'),
    lambda s:s.replace('Other Activity $0.00','Other Activity missing'),
])
def test_conflicts_or_unclassified_cash_withhold_income(change):
    with pytest.raises(ValueError):parse(change(text()))

def test_zero_income_active_month_is_verified_only_with_complete_controls():
    result=parse(text(dividends='0.00',interest='0.00',charged='0.00',fees='0.00'))
    assert result.net_income==0 and result.items==()
    with pytest.raises(ValueError):parse('January 1-31, 2025\nNo activity')

def test_reinvestment_trade_and_dividend_are_not_both_counted_as_income():
    result=parse(text(purchases='-1059.00').replace('Qual. Dividend','Reinvest Dividend'))
    assert result.dividends==59 and result.net_income==D('56.75')

def test_dividend_reversal_is_signed_and_not_discarded():
    result=parse(text(dividends='-59.00'))
    assert result.dividends==-59

def test_non_cash_activity_withholds_account_return_but_not_verified_income():
    result=parse(text(other='200.00'))
    assert result.net_income==D('56.75') and result.account_investment_result is None and result.simple_return is None

def test_duplicate_real_payment_rows_are_not_deduplicated_or_silently_accepted():
    line='01/20 Dividend Qual. Dividend SPY SYNTHETIC ETF $59.00\n'
    with pytest.raises(ValueError):parse(text().replace(line,line+line))

def test_offsetting_external_cash_flows_still_withhold_percentage_return():
    result=parse(text(deposits='200.00',withdrawals='-200.00'))
    assert result.account_investment_result==1000 and result.simple_return is None
