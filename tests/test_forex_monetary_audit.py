"""Summary rounding must never waive individual execution or source errors."""
from datetime import date
from decimal import Decimal as D
import csv
import io
import json
import subprocess
import sys

import pytest
import campaigniq.forex_monetary_audit as module

END = date(2025, 8, 31)
HEADER = ('Customer Account Summary Report since Thursday, July 31, 2025 16:00:00 (CST) '
          'through Friday, August 29, 2025 16:00:00 (CST)')


@pytest.fixture
def evidence(tmp_path):
    text = '\n'.join([HEADER, 'Monthly Equity Summary',
        'Beginning Cash Balance   $1000.00', 'Deposits   $0.00',
        'Withdrawals   $0.00', 'Adjustments   $0.00',
        'Financing   $1.00', 'Settled Profit/Loss   ($9.99)',
        'Ending Cash Balance   $991.00   1234567   USD',
        'Monthly Profit/Loss Summary', 'EUR/USD   ($10.00)',
        'Open Position(s) at End of Month', 'Floating Profit/Loss Net USD : $0.00', '\f',
        'Trade Activity Detail',
        '2025-08-07 07:02:50 EUR/USD LIMIT B CLOSE 50,000 1.166891 12345 $3.00',
        'Financing Report',
        '2025-08-06 17:13:15 EUR/USD (50,000) 1.16600 67890 $1.00000 $1.00',
        'Conversion Report'])
    output = io.StringIO(); w = csv.writer(output)
    for row in [
        ['Transaction Report since Jul 31, 2025 16:00:00 (EDT)  through Aug 31, 2025 16:00:00 (EDT)'],
        ['Account (USD): 1234567, | Base currency: (USD)'],
        ['MTD Settled PL, USD:', '-9.99'], ['MTD fee, USD:', '0.00'],
        ['="12345"', 'Aug 7, 2025 09:02:50', 'Aug 7, 2025 17:00:00', 'settlement',
         'EUR/USD', 'Buy', '1.166891', '50000', '-58344.55', '0.00', '', '-10.00 USD', '0'],
        ['="67890"', 'Aug 6, 2025 17:13:15', 'Aug 6, 2025 17:13:15', 'financing',
         'USD$FX', '', '1.166', '', '', '', '', '', '', '1.00', '+1.00 USD'],
        ['PL Total*, USD:', '', '-9.99'],
    ]:
        w.writerow(row)
    report = tmp_path / 'report.csv'; report.write_text(output.getvalue())
    history = tmp_path / 'history.csv'
    history.write_text('\n'.join(['Account Statement synthetic', '', 'Cash Balance',
        'DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,Balance',
        '8/28/25,18:18:54,DOI,="99999",SCHWAB1 INT 07/30-08/27 38.68 US$,,,38.68,1000', '',
        'Forex Statements', ',Date,Time,Type,Ref #,Description,Commissions & Fees,Amount,Amount(USD),Balance',
        ',8/7/25,05:02:50,TRD,="12345","BOT +50,000 EUR/USD @1.166891",--,-10.00,($10.00),991', '', '']))
    return dict(monthly_text=text, transaction_report=report, trade_history=history,
                period_end=END, development_only=True)


def audit(evidence, **kwargs):
    before = (evidence['transaction_report'].read_bytes(), evidence['trade_history'].read_bytes())
    result = module.audit_forex_money(**dict(evidence, **kwargs))
    assert before == (evidence['transaction_report'].read_bytes(), evidence['trade_history'].read_bytes())
    return result


@pytest.mark.parametrize('difference,accepted', [
    ('0', True), ('0.01', True), ('-0.01', True), ('0.01000001', False),
    ('-0.01000001', False), ('0.02', False), ('0.38', False), ('2577.41', False),
])
def test_summary_tolerance_is_exactly_one_cent(difference, accepted):
    control = module.summary_control(D(difference), D(0))
    assert control['within_tolerance'] is accepted
    assert control['difference_usd'] == str(D(difference))


@pytest.mark.parametrize('invalid', ['NaN', 'Infinity', '-Infinity'])
def test_nonfinite_summary_cannot_pass(invalid):
    with pytest.raises(ValueError, match='finite Decimal'):
        module.summary_control(D(invalid), D(0))


def test_one_cent_summary_difference_passes_without_rewriting_values(evidence):
    result = audit(evidence)
    assert result['monthly_money_controls_verified'] is True
    controls = result['summary_controls']
    assert controls['monthly_settled_total_vs_pairs']['difference_usd'] == '0.01'
    assert controls['monthly_cash_balance_bridge']['difference_usd'] == '-0.01'
    assert controls['monthly_settled_total_vs_pairs']['rounding_applied'] is True
    assert result['rounding_policy']['execution_quantity_rate_tolerance'] == '0'
    assert not result['publication_authorized'] and not result['csv_ready_for_import']
    assert not result['full_continuity_verified']
    json.dumps(result)


def test_two_cent_monthly_difference_is_a_blocker(evidence):
    result = audit(evidence, monthly_text=evidence['monthly_text'].replace('Settled Profit/Loss   ($9.99)', 'Settled Profit/Loss   ($9.98)'))
    assert not result['monthly_money_controls_verified']
    assert any('monthly_settled_total_vs_pairs' in f for f in result['blocking_findings'])


def test_financing_id_amount_difference_is_exact_even_at_one_cent(evidence):
    result = audit(evidence, monthly_text=evidence['monthly_text'].replace('$1.00000 $1.00', '$1.00000 $1.01'))
    assert result['changed_financing_ids'] == ['67890']
    assert any('financing IDs/amounts' in f for f in result['blocking_findings'])


def test_missing_execution_is_not_rounding(evidence):
    path = evidence['transaction_report']
    lines = path.read_text().splitlines()
    path.write_text('\n'.join(line for line in lines if ',settlement,' not in line))
    result = audit(evidence)
    assert len(result['missing_csv_closes']) == 1
    assert result['missing_close_pl_usd'] == '-10.00'
    assert result['monthly_money_controls_verified']
    assert not result['execution_comparison']['monthly_vs_csv_match']


@pytest.mark.parametrize('old,new', [('1.166891', '1.166892'), ('50000', '49999')])
def test_six_decimal_rate_and_quantity_comparisons_remain_exact(evidence, old, new):
    p = evidence['transaction_report']; p.write_text(p.read_text().replace(old, new))
    result = audit(evidence)
    assert not result['execution_comparison']['monthly_vs_csv_match']
    assert result['blocking_findings']


def test_one_cent_individual_settlement_difference_remains_visible(evidence):
    p = evidence['transaction_report']; p.write_text(p.read_text().replace('-10.00 USD', '-10.01 USD'))
    result = audit(evidence)
    assert result['settlement_usd_differences'][0]['difference_usd'] == '0.01'
    assert any('Individual settlement' in f for f in result['blocking_findings'])


def test_large_mtd_difference_cannot_use_summary_tolerance(evidence):
    p = evidence['transaction_report']; p.write_text(p.read_text().replace('-9.99', '-9.61', 1))
    result = audit(evidence)
    assert not result['summary_controls']['csv_mtd_vs_monthly_total']['within_tolerance']
    assert result['summary_controls']['csv_mtd_vs_monthly_total']['difference_usd'] == '0.38'


def test_cash_interest_classification_requires_matching_broker_reference_and_amount(evidence):
    p = evidence['transaction_report']
    p.write_text(p.read_text() + '="99999","Aug 28, 2025 20:18:54","Aug 28, 2025 20:18:54",financing,US$,,0,,,,,,,+38.68,+38.68 USD\n')
    result = audit(evidence)
    extra = result['additional_csv_financing_rows'][0]
    assert extra['classification'] == 'cash_interest_in_securities_cash_section'
    assert extra['excluded_from_import'] is False


def test_unmatched_cash_interest_is_not_automatically_excluded(evidence):
    p = evidence['transaction_report']
    p.write_text(p.read_text() + '="99998","Aug 28, 2025 20:18:54","Aug 28, 2025 20:18:54",financing,US$,,0,,,,,,,+38.68,+38.68 USD\n')
    result = audit(evidence)
    assert result['additional_csv_financing_rows'][0]['classification'] == 'absent_from_monthly_statement'


def test_duplicate_monthly_financing_is_rejected(evidence):
    line = '2025-08-06 17:13:15 EUR/USD (50,000) 1.16600 67890 $1.00000 $1.00'
    with pytest.raises(ValueError, match='Duplicate monthly financing'):
        audit(evidence, monthly_text=evidence['monthly_text'].replace(line, line + '\n' + line))


def test_missing_history_dollar_amount_is_not_invented(evidence):
    p = evidence['trade_history']; p.write_text(p.read_text().replace('($10.00)', '--'))
    with pytest.raises(ValueError, match='explicit matching history'):
        audit(evidence)


def test_wrong_account_blocks(evidence):
    p = evidence['transaction_report']; p.write_text(p.read_text().replace('1234567', '7654321'))
    with pytest.raises(ValueError, match='same account'):
        audit(evidence)


def test_stale_source_file_is_rejected(evidence, monkeypatch):
    real = module._monthly_money
    def changed(text):
        result = real(text)
        p = evidence['transaction_report']; p.write_text(p.read_text() + '\n')
        return result
    monkeypatch.setattr(module, '_monthly_money', changed)
    with pytest.raises(ValueError, match='changed during monetary audit'):
        audit(evidence)


def test_out_of_coverage_financing_date_is_not_included(evidence):
    with pytest.raises(ValueError, match='financing date is outside'):
        audit(evidence, monthly_text=evidence['monthly_text'].replace('2025-08-06 17:13:15', '2025-09-06 17:13:15'))


@pytest.mark.parametrize('flag', [False, None, 1])
def test_development_only_flag_is_required(evidence, flag):
    with pytest.raises(ValueError, match='development-only'):
        audit(evidence, development_only=flag)


@pytest.mark.parametrize('key', ['RENDER', 'RENDER_SERVICE_ID'])
def test_render_is_prohibited(evidence, monkeypatch, key):
    monkeypatch.setenv(key, 'true')
    with pytest.raises(ValueError, match='outside Render'):
        audit(evidence)


def test_cli_missing_evidence_cannot_authorize_publication(tmp_path):
    p = str(tmp_path / 'missing')
    r = subprocess.run([sys.executable, '-m', 'campaigniq.forex_monetary_audit',
        '--development-only', '--period-end', '2025-08-31', '--monthly-statement', p,
        '--transaction-report', p, '--trade-history', p], capture_output=True, text=True)
    assert r.returncode == 1 and json.loads(r.stdout)['publication_authorized'] is False
