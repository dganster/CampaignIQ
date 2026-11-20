"""Regression coverage for atomic economics selection and displayed report scope."""
import ast
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from campaigniq.ui.campaign_economics_view import campaign_economics_scope
from campaigniq.ui.history_scope import scope_history


@pytest.mark.parametrize('failure', ['load', 'selection', 'entries'])
def test_failed_linked_history_never_exposes_partial_records_or_cash(monkeypatch, failure):
    from campaigniq.analytics import campaign_economics as economics
    from campaigniq.analytics import linked_campaign_history as linked
    from campaigniq.ui import position_history_view as history
    current = (object(),)
    wider = (object(), object())
    monkeypatch.setattr(economics, 'campaign_record_scope', lambda *a: (wider, True))
    monkeypatch.setattr(history, 'campaign_entries', lambda *a: ['wide cash evidence'])
    def reject(): raise ValueError('unverified source')
    reader = SimpleNamespace(
        selection=lambda *a: reject() if failure == 'selection' else {'records': wider},
        entries=lambda *a: reject() if failure == 'entries' else ['linked cash evidence'])
    monkeypatch.setattr(linked, 'load_linked_history', lambda *a: reject() if failure == 'load' else reader)
    records, entries, lifetime, status = campaign_economics_scope(
        object(), {}, '2026-10/2026-09:DELL:CAMP-3', [], current, ['selected history'])
    assert records is current
    assert entries == () and lifetime is False and status == 'unavailable'


@pytest.mark.parametrize('present', [False, True])
def test_successful_history_keeps_exact_identity_or_verified_link_scope(monkeypatch, present):
    from campaigniq.analytics import campaign_economics as economics
    from campaigniq.analytics import linked_campaign_history as linked
    from campaigniq.ui import position_history_view as history
    identity = (object(),); verified = (object(),)
    monkeypatch.setattr(economics, 'campaign_record_scope', lambda *a: (identity, True))
    monkeypatch.setattr(history, 'campaign_entries', lambda *a: ['identity entries'])
    reader = SimpleNamespace(selection=lambda *a: {'records': verified}, entries=lambda *a: ['verified entries'])
    monkeypatch.setattr(linked, 'load_linked_history', lambda *a: reader if present else None)
    result = campaign_economics_scope(object(), {}, '2026-10/C1', [], (), [])
    assert result == (verified, ['verified entries'], True, 'linked') if present else result == (identity, ['identity entries'], True, 'identity')


def dashboard_tree():
    return ast.parse((Path(__file__).parents[1] / 'src/campaigniq/ui/dashboard.py').read_text())


@pytest.mark.parametrize('futures', [False, True])
def test_actual_monthly_table_exposes_every_included_market_without_changing_values(futures):
    # Execute the production dataframe selection and renaming statements.
    nodes = [n for n in ast.walk(dashboard_tree()) if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == 'display_df' for t in n.targets)]
    select = next(n for n in nodes if isinstance(n.value, ast.Call) and 'equity_options_realized_pnl' in ast.unparse(n))
    rename = next(n for n in ast.walk(dashboard_tree()) if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                          and t.value.id == 'display_df' and t.attr == 'columns' for t in n.targets))
    row = dict(month='Oct 2026', equity_options_realized_pnl=19020.47, forex_settled_pnl=1376.84,
               realized_pnl=16603.71 if futures else 20397.31, campaigns=27, wins=23,
               losses=4, breakeven=0, win_rate=23/27, records=43)
    if futures: row['futures_realized_pnl'] = -3793.60
    frame = pd.DataFrame([row]); before = frame.copy(deep=True)
    env = dict(df=frame, futures_applicable=futures)
    exec(compile(ast.Module(body=[select, rename], type_ignores=[]), '<monthly-table>', 'exec'), env)
    output = env['display_df'].iloc[0]
    parts = output['Equity/Options P&L'] + output['FOREX Settled P&L']
    if futures: parts += output['Futures/options Net P&L']
    else: assert 'Futures/options Net P&L' not in output
    assert parts == pytest.approx(output['Combined Realized P&L'])
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize('all_history', [False, True])
def test_actual_lifecycle_table_consumes_filtered_rows(all_history):
    rows = [dict(Date=date(2025, 8, 14), Time=None, Transition='ROLL'),
            dict(Date=date(2026, 10, 27), Time=None, Transition='ROLL')]
    _, scoped = scope_history([], rows, '2026-10', '2026-10', all_history=all_history)
    # Evaluate the dataframe passed by the real lifecycle-events renderer.
    section = next(n for n in ast.walk(dashboard_tree()) if isinstance(n, ast.With)
                   and any(isinstance(x, ast.Constant) and x.value == 'Published Lifecycle Events'
                           for item in n.items for x in ast.walk(item.context_expr)))
    call = next(n for n in ast.walk(section) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == 'render_dataframe')
    table = eval(compile(ast.Expression(call.args[1]), '<lifecycle-table>', 'eval'),
                 dict(pd=pd, scoped_lifecycle_rows=scoped))
    assert list(table['Date']) == ([r['Date'] for r in rows] if all_history else [date(2026, 10, 27)])
    assert rows[0]['Date'] == date(2025, 8, 14)
