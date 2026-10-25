"""Independent monthly evidence must diagnose, never silently repair, CSV gaps."""
from datetime import date
from decimal import Decimal
import csv
import io
import json
import sys
import pytest
from campaigniq.forex_supplemental_audit import (
    read_monthly_forex_evidence, audit_forex_supplement, main,
)

END = date(2025,8,31)
HEADER = ('Customer Account Summary Report since Thursday, July 31, 2025 16:00:00 (CST) '
          'through Friday, August 29, 2025 16:00:00 (CST)')
TRADE = '2025-08-07 07:02:50 USD/CAD LIMIT S CLOSE (50,000) 1.37265 12345 $2.18'


def monthly(trade=TRADE):
    return '\n'.join([HEADER, 'Monthly Equity Summary',
        'Settled Profit/Loss   ($424.41)   Account information',
        'Financing   $77.74', 'Open Position(s) at End of Month',
        'EUR/USD', 'TR Details: 8/27/2025 11:57:33 AM Sell (200,000) 1.1609300',
        'EUR/USD - TOTAL POSITION: Sell (200,000) ($1,604.00) $7,011.09',
        'Floating Profit/Loss Net USD : ($1,604.00)', '\f',
        'Customer Account Summary Report daily page', 'Trade Activity Detail', trade,
        'Financing Report', '2025-08-07 17:00:00 irrelevant financing line'])


def sources(tmp_path, *, include_csv_trade=False, history_quantity='-50,000'):
    history=tmp_path/'history.csv'
    history.write_text('\n'.join(['Account Statement synthetic', '', 'Forex Statements',
        'Trade Date,Exec Date,Exec Time,Type,Ref #,Description,Misc Fees,Fees,Amount,Balance',
        f',8/7/25,05:02:50,TRD,="12345","SOLD {history_quantity} USD/CAD @1.37265",--,--,--,0', '', '']))
    report=tmp_path/'report.csv'
    output=io.StringIO(); w=csv.writer(output)
    w.writerow(['Transaction Report since Jul 31, 2025 16:00:00 (EDT)  through Aug 31, 2025 16:00:00 (EDT)'])
    w.writerow(['MTD Settled PL, USD:','0.00']);w.writerow(['MTD fee, USD:','0.00'])
    if include_csv_trade:
        w.writerow(['="12345"','Aug 7, 2025 09:02:50','Aug 7, 2025 17:00:00','settlement',
            'USD/CAD','Sell','1.37265','-50,000','+68,632.5','0.00','','-424.41 USD','0'])
    report.write_text(output.getvalue())
    return report,history


def audit(tmp_path, **kwargs):
    report,history=sources(tmp_path,**kwargs)
    before=(report.read_bytes(),history.read_bytes())
    result=audit_forex_supplement(monthly_text=monthly(),transaction_report=report,
        trade_history=history,period_end=END,computed_positions={'EUR/USD':Decimal('-200000')})
    assert before == (report.read_bytes(),history.read_bytes())
    return result


def test_missing_csv_close_is_evidence_gap_even_when_closing_positions_match(tmp_path):
    result=audit(tmp_path)
    assert result['monthly_vs_history_match']
    assert not result['monthly_vs_csv_match']
    assert result['computed_closing_positions_match']
    assert result['monthly_trades_missing_from_csv'] == [dict(order_id='12345',
        date='2025-08-07',pair='USD/CAD',quantity='-50000',rate='1.37265')]
    assert result['blocking_findings']
    assert not result['publication_authorized'] and not result['full_continuity_verified']
    assert result['settled_pl_difference_usd']=='-424.41'
    json.dumps(result)


def test_matching_execution_does_not_hide_financing_control_difference(tmp_path):
    result=audit(tmp_path,include_csv_trade=True)
    assert result['monthly_vs_csv_match']
    assert result['settled_pl_difference_usd']=='0.00'
    assert result['financing_difference_usd']=='77.74'
    assert result['blocking_findings']==['Monthly financing differs from CSV financing rows.']


def test_history_quantity_disagreement_is_explicit(tmp_path):
    result=audit(tmp_path,history_quantity='-49,999')
    assert not result['monthly_vs_history_match']
    assert result['history_trades_missing_from_monthly']
    assert result['monthly_trades_missing_from_history']


def test_wrong_computed_position_remains_blocked(tmp_path):
    report,history=sources(tmp_path)
    result=audit_forex_supplement(monthly_text=monthly(),transaction_report=report,
        trade_history=history,period_end=END,computed_positions={'USD/CAD':Decimal('50000')})
    assert not result['computed_closing_positions_match']
    assert 'Computed Forex closing quantities differ from monthly statement.' in result['blocking_findings']


@pytest.mark.parametrize('replacement', ['August 28, 2025','August 31, 2025','September 1, 2025'])
def test_unsupported_coverage_is_not_guessed(replacement):
    with pytest.raises(ValueError,match='coverage'):
        read_monthly_forex_evidence(monthly().replace('August 29, 2025',replacement),period_end=END)


def test_month_end_is_required():
    with pytest.raises(ValueError,match='month-end'):
        read_monthly_forex_evidence(monthly(),period_end=date(2025,8,30))


@pytest.mark.parametrize('before,after', [
    ('Monthly Equity Summary','Daily Equity Summary'),
    ('Open Position(s) at End of Month','Open Position(s)'),
    ('Floating Profit/Loss Net USD : ($1,604.00)',''),
    ('Settled Profit/Loss   ($424.41)   Account information',''),
    ('Financing   $77.74','Financing   NaN'),
    ('Sell (200,000)','Sell 200,000'),
])
def test_incomplete_or_invalid_summary_fails_closed(before,after):
    with pytest.raises(ValueError):
        read_monthly_forex_evidence(monthly().replace(before,after),period_end=END)


def test_duplicate_positions_are_rejected():
    with pytest.raises(ValueError,match='Duplicate month-end'):
        read_monthly_forex_evidence(monthly().replace('Floating Profit/Loss Net USD',
            'EUR/USD - TOTAL POSITION: Sell (200,000) ($1,604.00) $7,011.09\nFloating Profit/Loss Net USD'),period_end=END)


@pytest.mark.parametrize('bad', [TRADE.replace('S CLOSE','B CLOSE'),TRADE.replace('1.37265','0'),
    TRADE.replace('CLOSE','UNKNOWN'),TRADE.replace('2025-08-07','2025-09-01')])
def test_bad_trade_rows_are_rejected(bad):
    with pytest.raises(ValueError):
        read_monthly_forex_evidence(monthly(bad),period_end=END)


def test_duplicate_trade_evidence_is_rejected():
    with pytest.raises(ValueError,match='Duplicate monthly execution'):
        read_monthly_forex_evidence(monthly(TRADE+'\n'+TRADE),period_end=END)


def test_identical_order_id_at_different_execution_time_is_not_collapsed():
    # This reader currently cannot distinguish same-day identical partial fills;
    # such ambiguous source evidence must be rejected, not deduplicated.
    with pytest.raises(ValueError,match='Duplicate monthly execution'):
        read_monthly_forex_evidence(monthly(TRADE+'\n'+TRADE.replace('07:02:50','07:02:51')),period_end=END)


def test_csv_wrong_period_stays_blocked(tmp_path):
    report,history=sources(tmp_path)
    report.write_text(report.read_text().replace('Aug 31, 2025','Sep 1, 2025'))
    with pytest.raises(ValueError,match='standard selected-month interval'):
        audit_forex_supplement(monthly_text=monthly(),transaction_report=report,trade_history=history,period_end=END)


def test_render_cli_is_rejected(monkeypatch):
    monkeypatch.setenv('RENDER','true')
    monkeypatch.setattr(sys,'argv',['audit','--development-only','--period-end','2025-08-31',
        '--monthly-statement','missing.pdf','--transaction-report','missing.csv','--trade-history','missing.csv'])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
