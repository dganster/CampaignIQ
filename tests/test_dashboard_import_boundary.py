"""Overview keeps chart behavior while avoiding the Altair compiler."""
from datetime import date
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd

from campaigniq.ui.campaign_interactions import build_month_chart, build_month_chart_spec, render_month_chart
from campaigniq.ui.account_dashboard_state import ScopedUI


def test_overview_spec_preserves_compiled_chart_and_selection():
    frame = pd.DataFrame([dict(period_start=date(2026, 10, 1), realized_pnl=-10.25),
                          dict(period_start=date(2025, 12, 1), realized_pnl=0.),
                          dict(period_start=date(2026, 1, 1), realized_pnl=42.5)])
    before = frame.copy(deep=True)
    chart, months = build_month_chart(frame)
    expected = chart.to_dict(validate=True)
    actual, actual_months = build_month_chart_spec(frame)
    records = expected.pop('datasets')[expected.pop('data')['name']]
    for row in records:
        row.pop('period_start')
    expected.pop('$schema')
    actual.pop('$schema')
    assert actual.pop('data') == {'values': records}
    assert actual == expected
    assert actual_months == months == ('2025-12', '2026-01', '2026-10')
    pd.testing.assert_frame_equal(frame, before)


def test_native_spec_callback_is_account_scoped_and_reopens_selected_month():
    class UI:
        def __init__(self): self.calls = []
        def vega_lite_chart(self, spec, **kwargs): self.calls.append(kwargs)
    native = UI(); state = {}
    a = ScopedUI(native, state, 'actor:a:'); b = ScopedUI(native, state, 'actor:b:')
    a.session_state['campaigniq_primary_view'] = 'Overview'
    b.session_state['campaigniq_primary_view'] = 'Overview'
    render_month_chart(a, {}, ('2026-10',))
    widget = native.calls[-1]
    assert widget['key'].startswith('actor:a:')
    state[widget['key']] = {'selection': {'campaign_month': [{'month_key': '2026-10'}]}}
    widget['on_select']()
    assert a.session_state['campaigniq_primary_view'] == 'Campaigns'
    assert a.session_state['campaigniq_campaign_month'] == '2026-10'
    assert b.session_state['campaigniq_primary_view'] == 'Overview'
    a.session_state['campaigniq_primary_view'] = 'Overview'
    render_month_chart(a, {}, ('2026-10',))
    assert native.calls[-1]['key'] != widget['key']


def test_cold_dashboard_and_native_chart_render_do_not_load_altair():
    code = '''
import sys
from datetime import date
import campaigniq.account_deployment_startup
from campaigniq.ui.account_dashboard_view import report_module
module = report_module()
import pandas as pd
import streamlit as st
frame = pd.DataFrame([dict(period_start=date(2026,10,1),realized_pnl=25.)])
spec, months = module.build_month_chart_spec(frame)
st.vega_lite_chart(spec, key='cold-chart', on_select='rerun', selection_mode='campaign_month')
st.dataframe(frame)
assert 'altair' not in sys.modules
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / 'src'))
    result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
