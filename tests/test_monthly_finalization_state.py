from types import SimpleNamespace
import pytest
from campaigniq.ui.monthly_finalization_state import run_or_resume_finalization


def result(reconciled):
    return SimpleNamespace(closing_reconciliation=SimpleNamespace(reconciled=reconciled))


def test_editing_exception_fields_resumes_review_without_executing():
    state = {}
    blocked = result(False)
    calls = []
    def execute():
        calls.append(1)
        return blocked
    assert run_or_resume_finalization(state, signature="May files", clicked=True, execute=execute) is blocked
    for field, value in [("reason", "Settlement crosses month end"), ("evidence", "Dated broker history"), ("approved", True)]:
        state[field] = value
        assert run_or_resume_finalization(state, signature="May files", clicked=False, execute=execute) is blocked
    assert len(calls) == 1


def test_changed_files_or_month_clear_the_old_review():
    state = {}
    run_or_resume_finalization(state, signature="May", clicked=True, execute=lambda: result(False))
    assert run_or_resume_finalization(state, signature="June", clicked=False, execute=lambda: pytest.fail("Unexpected execution")) is None
    assert "monthly_import_blocked_execution" not in state


def test_no_button_click_never_executes_or_publishes():
    assert run_or_resume_finalization({}, signature="May", clicked=False, execute=lambda: pytest.fail("Unexpected execution")) is None


def test_successful_retry_clears_failed_result():
    state = {}
    run_or_resume_finalization(state, signature="May", clicked=True, execute=lambda: result(False))
    success = result(True)
    assert run_or_resume_finalization(state, signature="May", clicked=True, execute=lambda: success) is success
    assert "monthly_import_blocked_execution" not in state


def test_failed_retry_cannot_leave_stale_review():
    state = {}
    run_or_resume_finalization(state, signature="May", clicked=True, execute=lambda: result(False))
    def fail():
        raise ValueError("Import failed")
    with pytest.raises(ValueError):
        run_or_resume_finalization(state, signature="May", clicked=True, execute=fail)
    assert "monthly_import_blocked_execution" not in state
