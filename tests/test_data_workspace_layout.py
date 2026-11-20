from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from tests.test_monthly_review_cleanup import screen


def test_empty_draft_hides_empty_history_and_recovery_without_selecting_activity():
    app = screen()
    assert not app.exception
    assert app.radio[0].value is None
    labels = [e.label for e in app.expander]
    assert 'Upload and recovery history' not in labels
    assert 'Correct this draft or start over' not in labels
    assert any(e.label == 'Start or resume a month' and not e.proto.expanded for e in app.expander)
    assert any(e.label == 'Month details' and not e.proto.expanded for e in app.expander)
    selected = next(s for s in app.selectbox if s.label == 'Saved month')
    assert selected.options == ['January 2025 · Draft']
    assert any('Draft · January 2025' in c.value for c in app.caption)


def test_nonempty_unpublished_draft_keeps_history_and_reviewed_correction():
    app = screen(saved=True)
    assert not app.exception
    labels = [e.label for e in app.expander]
    assert 'Upload and recovery history' in labels
    assert 'Correct this draft or start over' in labels
    assert any(b.label == 'Review removal' for b in app.button)
    assert not any(b.label == 'Remove from this draft' for b in app.button)


def test_published_month_has_read_only_status_and_no_correction_controls():
    app = screen(published=True, saved=True)
    assert not app.exception
    assert any('Published · January 2025' in s.value for s in app.success)
    assert any(b.label == 'View published results' for b in app.button)
    assert not any(e.label == 'Correct this draft or start over' for e in app.expander)
    assert not app.file_uploader and not app.radio


def test_view_only_empty_account_does_not_offer_an_unavailable_create_action():
    from tests.test_account_signedin_inputs_view import screen as inputs_screen
    app = inputs_screen(writable=False, draft=False)
    assert not app.exception
    assert any('No saved monthly drafts are available to view' in i.value for i in app.info)
    assert not any(e.label == 'Start or resume a month' for e in app.expander)
    assert not any(b.label == 'Create or resume draft' for b in app.button)


def test_retained_published_month_defaults_latest_and_keeps_exact_month_value():
    from tests import test_account_legacy_results_view as legacy
    with patch.object(legacy.AppTest, 'from_string', side_effect=lambda code: SimpleNamespace(run=lambda **kw: code)):
        code = legacy.screen()
    code = code.replace("('2025-01',)", "('2025-01', '2026-10')")
    app = AppTest.from_string(code).run(timeout=20)
    assert not app.exception
    selected = next(s for s in app.selectbox if s.label == 'Published month')
    assert selected.value == '2026-10'
    assert selected.options == ['October 2026', 'January 2025']
    selected.select('2025-01').run(timeout=20)
    assert not app.exception and app.session_state['calls'] == []
    assert next(s for s in app.selectbox if s.label == 'Published month').value == '2025-01'


def test_history_only_inventory_is_preserved_without_unusable_recovery():
    from campaigniq.ui.account_draft_recovery_view import render
    class UI:
        def __init__(self):
            self.expanders, self.tables = [], []
        def expander(self, label):
            self.expanders.append(label)
            return self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def table(self, rows):
            self.tables.append(rows)
        def __getattr__(self, name):
            return lambda *args, **kwargs: None
    inventory = dict(protected=False, documents=[dict(id='old', role='schwab_brokerage_statement',
                     purpose='Brokerage statement', filename='old.pdf', superseded=True)])
    before = deepcopy(inventory)
    ui = UI()
    roles = render(ui, None, None, 'account', None, key='draft', writable=True,
                   published=False, inventory=inventory)
    assert inventory == before and roles == set()
    assert ui.expanders == ['Upload and recovery history']
    assert ui.tables[0][0]['Status'] == 'Superseded'
