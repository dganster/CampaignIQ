import json
from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from campaigniq.ui.analytics_navigation import render_return_button
from campaigniq.ui.campaign_interactions import open_position_campaign
from tests.test_account_dashboard_view import app_code


class Query(dict):
    def to_dict(self): return dict(self)
    def from_dict(self, values): self.clear(); self.update(values)


class UI:
    def __init__(self, values):
        self.session_state = values
        self.query_params = Query()
    def button(self, label, **kwargs):
        self.label, self.back = label, kwargs['on_click']


@pytest.mark.parametrize('start,end,month,expected', [
    ('2026-03', '2026-10', '2026-08', ('2026-03', '2026-10')),
    ('2026-03', '2026-10', '2026-01', ('2026-01', '2026-10')),
    ('2026-03', '2026-10', '2026-12', ('2026-03', '2026-12')),
    (None, None, '2026-10', ('2026-10', '2026-10')),
])
def test_position_campaign_retains_exact_return_route_and_minimal_range(start, end, month, expected):
    values = dict(campaigniq_primary_view='Positions', campaigniq_range_start=start,
                  campaigniq_range_end=end, campaigniq_lifecycle_symbol='DELL',
                  campaigniq_history_scope='All retained history')
    before = deepcopy(values)
    ui = UI(values)
    campaign = month + '/DELL:campaign'
    open_position_campaign(ui, campaign, 'DELL')
    assert (values['campaigniq_range_start'], values['campaigniq_range_end']) == expected
    assert values['campaigniq_campaign_detail'] == campaign
    assert json.loads(values['campaigniq_return_route'])['view'] == 'Positions'
    render_return_button(ui, ('Overview', 'Campaigns', 'Performance', 'Positions'))
    assert ui.label == '← Back to Positions'
    ui.back()
    assert values['campaigniq_primary_view'] == 'Positions'
    assert values['campaigniq_range_start'] == before['campaigniq_range_start']
    assert values['campaigniq_range_end'] == before['campaigniq_range_end']
    assert values['campaigniq_lifecycle_symbol'] == 'DELL'
    assert values['campaigniq_history_scope'] == 'All retained history'


def test_invalid_campaign_month_does_not_change_navigation():
    ui = UI({'campaigniq_primary_view': 'Positions'})
    before = deepcopy(ui.session_state)
    open_position_campaign(ui, 'invalid/DELL', 'DELL')
    assert ui.session_state == before and ui.query_params == {}


def test_account_report_renders_and_executes_back_to_positions():
    code = app_code().replace('render(UI(),Service(),administration=None)', '''
prefix='account-dashboard:test-registry:owner:a:'
if not st.session_state.get('seeded_return'):
    import json
    st.session_state[prefix+'main-view']='Campaigns'
    st.session_state[prefix+'campaigniq_return_route']=json.dumps(dict(view='Positions', **{'from':'2025-01','through':'2025-01'}, position='SPY'))
    st.session_state['seeded_return']=True
render(UI(),Service(),administration=None)
''')
    app = AppTest.from_string(code).run(timeout=25)
    assert not app.exception
    back = next(b for b in app.button if b.label == '← Back to Positions')
    back.click().run(timeout=25)
    assert not app.exception
    assert next(r for r in app.radio if r.label == 'Navigate').value == 'Positions'
    prefix = 'account-dashboard:test-registry:owner:a:'
    assert app.session_state[prefix+'campaigniq_range_start'] == '2025-01'
    assert app.session_state[prefix+'campaigniq_range_end'] == '2025-01'
    next(s for s in app.selectbox if s.label == 'Brokerage account').select('b').run(timeout=25)
    assert not app.exception
    assert not any(b.label == '← Back to Positions' for b in app.button)
