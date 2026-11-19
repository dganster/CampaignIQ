from streamlit.testing.v1 import AppTest

from tests.test_account_dashboard_view import app_code


def test_report_titles_and_period_summary_are_not_repeated():
    app = AppTest.from_string(app_code()).run(timeout=25)
    for view, title in [('Overview', 'Portfolio Overview'), ('Campaigns', 'Campaigns'),
                        ('Performance', 'Performance'), ('Positions', 'Positions')]:
        next(r for r in app.radio if r.label == 'Navigate').set_value(view).run(timeout=25)
        assert not app.exception
        assert [t.value for t in app.title] == [title]
        assert not any(s.value == title for s in app.subheader)
        periods = [c.value for c in app.caption if c.value.startswith('Reporting period:')]
        assert len(periods) == (0 if view == 'Positions' else 1)
        assert any(e.label == 'Report details' and not e.proto.expanded for e in app.expander)


def test_period_help_and_navigation_preserve_current_selection():
    app = AppTest.from_string(app_code()).run(timeout=25)
    start = next(s for s in app.selectbox if s.label == 'From month').value
    end = next(s for s in app.selectbox if s.label == 'Through month').value
    year = next(s for s in app.selectbox if s.label == 'Shortcut year')
    assert 'does not change' in year.proto.help
    for view in ('Campaigns', 'Performance', 'Overview'):
        next(r for r in app.radio if r.label == 'Navigate').set_value(view).run(timeout=25)
        assert not app.exception
        assert next(s for s in app.selectbox if s.label == 'From month').value == start
        assert next(s for s in app.selectbox if s.label == 'Through month').value == end


def test_missing_history_source_remains_a_visible_warning():
    code = app_code().replace(
        "        return ReportSnapshot(account_id,_candidate('12345678',double=account_id=='b'))",
        """        from campaigniq.account_history_evidence import HistoryEvidence
        snapshot = ReportSnapshot(account_id,_candidate('12345678',double=account_id=='b'))
        snapshot.history = HistoryEvidence(gaps=('2025-01: Matching saved original is unavailable',), published_months=('2025-01',))
        return snapshot""",
    )
    app = AppTest.from_string(code).run(timeout=25)
    assert not app.exception
    assert any('supplementary trade-history source details are unavailable' in w.value for w in app.warning)
    assert any('trade-history originals only' in c.value for c in app.caption)
    assert any('Matching saved original is unavailable' in c.value for c in app.caption)
    assert any(e.label == 'Account source details' and not e.proto.expanded for e in app.expander)


def test_overview_keeps_realized_metrics_and_scope():
    app = AppTest.from_string(app_code()).run(timeout=25)
    assert not app.exception
    assert any(m.label == 'Trading realized P&L' for m in app.metric)
    assert any('Statement income, financing, unrealized changes, and crypto are excluded' in c.value for c in app.caption)
