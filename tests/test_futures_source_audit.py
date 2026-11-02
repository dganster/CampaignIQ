"""Synthetic evidence; no customer data or database access."""
import csv
from io import StringIO
import json
import subprocess
import sys

import pytest

from campaigniq.futures_source_audit import audit, money

TEXT = '''MONTHLY STATEMENT
*SEGREGATED USD* *CONVERTED TOTAL*
STATEMENT DATE: OCT 30, 2026
BEGINNING BALANCE .00 .00
THIS MONTH'S ACTIVITY 85.00 85.00
ENDING BALANCE 85.00 85.00
NET FUTURES PROFIT OR LOSS 10.00- 10.00-
NET OPTION PREMIUM 95.00 95.00
TOTAL COMMISSION AND FEES 5.00- 5.00-
'''
HEADER = ['', 'Exec Time', 'Spread', 'Side', 'Qty', 'Pos Effect', 'Symbol', 'Exp', 'Strike', 'Type', 'Price', 'Net Price', 'Order Type']
LEGS = [
    ['', '10/5/26 07:43:41', 'VERTICAL', 'SELL', '-2', 'TO OPEN', '/ESZ26 1/50 30 OCT 26 (EOM)', '/EWV26', '7735', 'PUT', '48.95', '2.20', 'LMT'],
    ['', '', '', 'BUY', '+2', 'TO OPEN', '/ESZ26 1/50 30 OCT 26 (EOM)', '/EWV26', '7730', 'PUT', '46.75', 'CREDIT', ''],
]
CASH_HEADER = ['Trade Date', 'Exec Date', 'Exec Time', 'Type', 'Ref #', 'Description', 'Misc Fees', 'Commissions & Fees', 'Amount', 'Balance']
CASH = [
    ['9/30/26', '10/1/26', '00:00:00', 'BAL', '--', 'Opening balance', '--', '--', '--', '0.00'],
    ['10/4/26', '10/5/26', '07:43:41', 'TRD', '123', 'SOLD vertical', '-2.00', '-3.00', '100.00', '95.00'],
    ['10/29/26', '10/30/26', '14:00:00', 'TRD', '124', 'Opening futures position due to futures option expiration', '--', '--', '-10.00', '85.00'],
    ['10/31/26', '10/31/26', '23:00:00', 'BAL', '--', 'Daily balance', '--', '--', '--', '85.00'],
]


def history(tmp_path, legs=None, cash=None):
    stream = StringIO()
    stream.write('Account Statement for synthetic (private)\n\nAccount Trade History\n')
    writer = csv.writer(stream)
    writer.writerows([HEADER, *(LEGS if legs is None else legs)])
    stream.write('\nFutures Statements\n')
    writer.writerows([CASH_HEADER, *(CASH if cash is None else cash)])
    stream.write('\n')
    path = tmp_path / 'source.csv'
    path.write_text(stream.getvalue())
    return path


def test_cash_controls_and_series_are_separate_without_authorizing_import(tmp_path):
    source = history(tmp_path)
    before = source.read_bytes()
    result = audit(source, TEXT, year=2026, month=10)
    assert result['cash_reconciled'] and all(result['checks'].values())
    assert result['futures_option_legs'] == 2 and result['series'] == ['/EWV26']
    assert result['diagnostic_only'] and result['publication_authorized'] is False
    assert source.read_bytes() == before
    assert 'private' not in json.dumps(result)


@pytest.mark.parametrize('field,value', [('Exp', '/BAD SERIES'), ('Symbol', '/ESZ26 unknown'), ('Qty', 'NaN'), ('Qty', '+2'), ('Type', 'FUTURE'), ('Strike', '-1'), ('Price', 'Infinity')])
def test_unrecognized_or_invalid_futures_leg_is_rejected(tmp_path, field, value):
    legs = [r.copy() for r in LEGS]
    legs[0][HEADER.index(field)] = value
    with pytest.raises(ValueError):
        audit(history(tmp_path, legs=legs), TEXT, year=2026, month=10)


def test_orphan_continuation_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='timestamp'):
        audit(history(tmp_path, legs=[LEGS[1]]), TEXT, year=2026, month=10)


@pytest.mark.parametrize('row,col,value', [(1, 'Balance', '94'), (3, 'Balance', '84'), (1, 'Type', 'UNKNOWN'), (0, 'Amount', '1')])
def test_running_cash_or_unknown_row_cannot_be_silently_accepted(tmp_path, row, col, value):
    rows = [r.copy() for r in CASH]
    rows[row][CASH_HEADER.index(col)] = value
    with pytest.raises(ValueError):
        audit(history(tmp_path, cash=rows), TEXT, year=2026, month=10)


def test_missing_opening_checkpoint_fails(tmp_path):
    with pytest.raises(ValueError, match='opening'):
        audit(history(tmp_path, cash=CASH[1:]), TEXT, year=2026, month=10)


@pytest.mark.parametrize('changed', [
    TEXT.replace('OCT 30', 'SEP 30'),
    TEXT + '\nSTATEMENT DATE: OCT 29, 2026',
    TEXT.replace('ENDING BALANCE 85.00 85.00', 'ENDING BALANCE 86.00 86.00'),
    TEXT.replace('ENDING BALANCE 85.00 85.00', 'ENDING BALANCE 85.00 86.00'),
])
def test_wrong_period_conflict_or_invalid_pdf_controls_fail(tmp_path, changed):
    with pytest.raises(ValueError):
        audit(history(tmp_path), changed, year=2026, month=10)


def test_independent_fee_mismatch_is_reported_without_approval(tmp_path):
    result = audit(history(tmp_path), TEXT.replace('5.00-', '6.00-'), year=2026, month=10)
    assert result['checks']['total_fees'] is False and result['cash_reconciled'] is False
    assert result['publication_authorized'] is False


def test_cli_missing_evidence_is_json_failure(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'campaigniq.futures_source_audit',
        '--trade-history', str(tmp_path/'missing.csv'), '--futures-statement', str(tmp_path/'missing.pdf'),
        '--year', '2026', '--month', '10'], capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)['publication_authorized'] is False


def test_money_rejects_nonfinite_and_reads_trailing_negative():
    assert str(money('1,239.40-')) == '-1239.40'
    with pytest.raises(ValueError):
        money('NaN')
