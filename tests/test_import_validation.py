from datetime import date
from campaigniq.import_contract import MonthlyInputRole
from campaigniq.import_validation import validate_monthly_input

START = date(2026, 8, 1)
END = date(2026, 8, 31)

def test_recognizes_august_trade_history_by_content() -> None:
    result = validate_monthly_input(
        MonthlyInputRole.THINKORSWIM_TRADE_HISTORY,
        "tests/data/thinkorswim/Account Trade History August 2026.csv",
        period_start=START, period_end=END,
    )
    assert result.valid is True
    assert result.record_count > 0
    assert "in-period trades" in result.message

def test_rejects_trade_history_without_requested_month_activity() -> None:
    result = validate_monthly_input(
        MonthlyInputRole.THINKORSWIM_TRADE_HISTORY,
        "tests/data/thinkorswim/Account Trade History July 2026.csv",
        period_start=START, period_end=END,
    )
    assert result.valid is False
    assert "no trades in the requested month" in result.message

def test_recognizes_august_realized_report_and_exact_date_range() -> None:
    result = validate_monthly_input(
        MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS,
        "tests/data/schwab/august_realized_gain_loss.txt",
        period_start=START, period_end=END,
    )
    assert result.valid is True
    assert result.record_count == 62
    assert "requested month" in result.message

def test_rejects_realized_report_for_wrong_month() -> None:
    result = validate_monthly_input(
        MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS,
        "tests/data/schwab/july_realized_gain_loss.txt",
        period_start=START, period_end=END,
    )
    assert result.valid is False
    assert "requested period" in result.message

def test_recognizes_august_assignment_evidence() -> None:
    result = validate_monthly_input(
        MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE,
        "tests/data/schwab/august_assignments.txt",
        period_start=START, period_end=END,
    )
    assert result.valid is True
    assert result.record_count == 1

def test_rejects_missing_file_cleanly() -> None:
    result = validate_monthly_input(
        MonthlyInputRole.THINKORSWIM_TRADE_HISTORY,
        "tests/data/does-not-exist.anything",
        period_start=START, period_end=END,
    )
    assert result.valid is False
    assert result.message == "File does not exist."


def test_recognizes_august_closing_position_snapshot() -> None:
    result = validate_monthly_input(
        MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT,
        "tests/data/schwab/august_positions.txt",
        period_start=START, period_end=END,
    )
    assert result.valid is True
    assert result.record_count > 0
    assert "month-end position snapshot" in result.message


def test_rejects_closing_position_snapshot_for_wrong_month() -> None:
    result = validate_monthly_input(
        MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT,
        "tests/data/schwab/june_positions.txt",
        period_start=START, period_end=END,
    )
    assert result.valid is False
    assert "requested Schwab statement period" in result.message


def test_accepts_next_weekday_assignment_as_boundary_evidence(tmp_path) -> None:
    evidence = tmp_path / "boundary.txt"
    evidence.write_text(
        "09/01\n"
        "Other Activity\n"
        "Option Assignment\n"
        "IBM\n"
        "09/18/2026 250.00 C\n"
        "IBM CORP\n"
        "1.0000\n"
    )
    result = validate_monthly_input(
        MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE,
        evidence,
        period_start=START, period_end=END,
    )
    assert result.valid is True
    assert result.record_count == 1
    assert "boundary-date" in result.message


def test_validates_schwab_forex_transaction_report(tmp_path):
    path=tmp_path/"fx.csv"
    path.write_text('"Transaction Report since Jul 31, 2026 16:00:00 (EDT) through Aug 31, 2026 16:00:00 (EDT)"\n"MTD Settled PL, USD:",+3.00\n"MTD fee, USD:",0.00\n=\"1007745626983\","Aug 27, 2026 20:19:57","Aug 28, 2026 17:00:00",settlement,EUR/USD,Sell,1.16508,"-100,000","+116,508",0.00,,"+3.00 USD",0,,"+3.00 USD"\n')
    result=validate_monthly_input(MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT,path,period_start=date(2026,8,1),period_end=date(2026,8,31))
    assert result.valid is True
    assert result.record_count == 1

def test_rejects_forex_transaction_report_for_wrong_month(tmp_path):
    path=tmp_path/"fx.csv"
    path.write_text('"Transaction Report since Jun 30, 2026 through Jul 31, 2026"\n"MTD Settled PL, USD:",0.00\n"MTD fee, USD:",0.00\n')
    result=validate_monthly_input(MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT,path,period_start=date(2026,8,1),period_end=date(2026,8,31))
    assert result.valid is False


def test_rejects_forex_report_with_requested_month_name_but_wrong_boundaries(tmp_path):
    path=tmp_path/"fx_wrong_boundaries.csv"
    path.write_text('"Transaction Report since Aug 1, 2026 through Aug 31, 2026"\n"MTD Settled PL, USD:",0.00\n"MTD fee, USD:",0.00\n')
    result=validate_monthly_input(
        MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT, path,
        period_start=date(2026,8,1), period_end=date(2026,8,31),
    )
    assert result.valid is False
    assert "2026-08-01 through 2026-08-31" in result.message
    assert "2026-07-31 through 2026-08-31" in result.message


def test_forex_validation_counts_new_transactions(tmp_path):
    path = tmp_path / "fx-with-new.csv"
    path.write_text(
        '"Transaction Report since Jul 31, 2026 through Aug 31, 2026"\n'
        '"MTD Settled PL, USD:",0.00\n'
        '"MTD fee, USD:",0.00\n'
        '="100","Aug 27, 2026 15:05:18","Aug 27, 2026 17:00:00",new,EUR/USD,Buy,1.16505,"+100,000","-116,505",0.00,1.16505,,"+100,000",,\n'
    )
    result = validate_monthly_input(
        MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT,
        path,
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
    )
    assert result.valid is True
    assert result.record_count == 1
    assert "1 new transaction(s)" in result.message
