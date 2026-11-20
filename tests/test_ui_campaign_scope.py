from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from campaigniq.ui.campaign_scope_view import render_metric_scope, render_list_scope
from tests.test_account_dashboard_view import app_code


class UI:
    def __init__(self):
        self.text = []
    def expander(self, label):
        return nullcontext()
    def caption(self, value):
        self.text.append(value)


@pytest.mark.parametrize('wins,losses,flat,count', [(15, 4, 1, 20), (0, 0, 0, 0)])
def test_denominator_is_reported_without_recalculating_or_mutating(wins, losses, flat, count):
    summary = SimpleNamespace(winning_campaign_count=wins, losing_campaign_count=losses,
                              breakeven_campaign_count=flat, campaign_count=count,
                              excluded_campaign_count=2)
    before = deepcopy(summary.__dict__)
    ui = UI()
    render_metric_scope(ui, summary)
    assert summary.__dict__ == before
    text = " ".join(ui.text)
    assert f'{wins} winning / {count} included campaign-month results' in text
    assert 'Breakevens remain in the denominator' in text
    assert 'not a count of fully closed campaigns' in text
    assert 'Forex, futures, and crypto are excluded' in text
    assert '2 campaign-month results failed recorded close checks' in text


def test_list_scope_distinguishes_equity_and_forex_counts():
    ui = UI()
    render_list_scope(ui, equity_rows=27, forex_rows=3)
    assert '27 equity/options and 3 Forex' in ' '.join(ui.text)
    assert 'before table filters' in ' '.join(ui.text)
    assert 'independent source verification' in ' '.join(ui.text)


def test_overview_labels_and_rate_value_remain_consistent():
    app = AppTest.from_string(app_code()).run(timeout=25)
    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert 'Equity/options campaigns' in metrics and 'Equity/options win rate' in metrics
    assert any('winning /' in c.value and 'Breakevens remain' in c.value for c in app.caption)
    assert not any(m.label == 'Campaign Win Rate' for m in app.metric)


@pytest.mark.parametrize('reconciled,forex,expected', [(True, False, 'Passed'), (False, False, 'Needs review'), (True, True, 'Settlement attribution')])
def test_production_campaign_table_uses_narrow_recorded_checks_label(reconciled, forex, expected):
    import ast
    from pathlib import Path
    from datetime import date
    from decimal import Decimal
    import pandas as pd
    source = (Path(__file__).parents[1] / 'src/campaigniq/ui/dashboard.py').read_text()
    tree = ast.parse(source)
    assignment = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'campaign_table' for t in n.targets))
    item = SimpleNamespace(campaign_id='id', symbols=('SPY',), realized_pnl=Decimal('12.34'),
                           first_closed_date=date(2026,10,1), last_closed_date=date(2026,10,2), fully_reconciled=reconciled)
    values = dict(pd=pd, visible=[item], forex_campaign_records={'id': ()} if forex else {})
    exec(compile(ast.Module(body=[assignment], type_ignores=[]), '<actual campaign table>', 'exec'), values)
    frame = values['campaign_table']
    assert frame.iloc[0]['Recorded close checks'] == expected
    assert frame.iloc[0]['Realized P&L'] == 12.34
    assert 'Reconciled' not in frame.columns
    app = AppTest.from_string(app_code()).run(timeout=25)
    next(r for r in app.radio if r.label == 'Navigate').set_value('Campaigns').run(timeout=25)
    assert not app.exception
    assert any('Available drilldown rows before table filters' in c.value for c in app.caption)
    assert any('known opening cost for every lot' in c.value for c in app.caption)


def test_monthly_performance_retains_explicit_scope_caption():
    app = AppTest.from_string(app_code()).run(timeout=25)
    next(r for r in app.radio if r.label == 'Navigate').set_value('Performance').run(timeout=25)
    assert not app.exception
    assert any('Campaigns, wins, losses, breakeven and win rate in this table cover equity/options' in c.value for c in app.caption)
