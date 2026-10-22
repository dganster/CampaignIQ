from datetime import date
from pathlib import Path
from campaigniq.historical_backfill_guard import assess_backfill


def test_backfill_detects_later_authoritative_periods():
    result = assess_backfill(date(2025, 1, 31), [date(2026, 8, 31), date(2025, 3, 31)])
    assert result.blocked
    assert len(result.later_periods) == 2
    assert "Historical backfill blocked" in result.message


def test_existing_month_is_blocked():
    result = assess_backfill(date(2026, 8, 31), [date(2026, 8, 31)])
    assert result.blocked and result.existing_period


def test_next_month_is_not_blocked():
    result = assess_backfill(date(2026, 9, 30), [date(2026, 8, 31)])
    assert not result.blocked


def test_guard_precedes_finalization_and_exception_path():
    dashboard = Path(__file__).parents[1] / 'src/campaigniq/ui/dashboard.py'
    text = dashboard.read_text()
    start = text.index('        # Backfill is intentionally blocked')
    confirm = text.index('        confirm = st.checkbox(', start)
    normal = text.index('            execution = run_or_resume_finalization(', confirm)
    exceptional = text.index('                execution = execute_monthly_import(', normal)
    assert start < confirm < normal < exceptional
    assert 'if backfill.blocked:' in text[start:confirm]
    assert 'return' in text[start:confirm]
