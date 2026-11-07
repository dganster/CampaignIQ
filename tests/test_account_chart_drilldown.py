"""Chart callbacks read the account-scoped selection written by Streamlit."""
from campaigniq.ui.account_dashboard_state import ScopedUI
from campaigniq.ui.campaign_interactions import render_month_chart


class ChartUI:
    def __init__(self): self.calls = []
    def altair_chart(self, chart, **kwargs): self.calls.append(kwargs)


def click(ui, state, month):
    render_month_chart(ui, object(), ('2026-09', '2026-10'))
    widget = ui._ui.calls[-1]
    state[widget['key']] = {'selection': {'campaign_month': [{'month_key': month}]}}
    widget['on_select']()
    return widget['key']


def test_overview_click_opens_selected_account_month_and_can_reopen():
    state = {}; native = ChartUI()
    a = ScopedUI(native, state, 'user:account-a:')
    b = ScopedUI(native, state, 'user:account-b:')
    for ui in (a, b):
        ui.session_state['campaigniq_primary_view'] = 'Overview'
        ui.session_state['campaigniq_campaign_symbol'] = 'SPY'
        ui.session_state['campaigniq_campaign_result'] = 'Loss'
    old_key = click(a, state, '2026-10')
    assert old_key.startswith('user:account-a:')
    assert a.session_state['campaigniq_primary_view'] == 'Campaigns'
    assert a.session_state['campaigniq_campaign_month'] == '2026-10'
    assert a.session_state['campaigniq_campaign_symbol'] == 'All symbols'
    assert a.session_state['campaigniq_campaign_result'] == 'All results'
    assert a.query_params['view'] == 'Campaigns'
    assert b.session_state['campaigniq_primary_view'] == 'Overview'
    assert b.session_state['campaigniq_campaign_symbol'] == 'SPY'
    assert 'campaigniq_campaign_month' not in b.session_state
    a.session_state['campaigniq_primary_view'] = 'Overview'
    assert click(a, state, '2026-10') != old_key
    assert a.session_state['campaigniq_primary_view'] == 'Campaigns'
    assert click(b, state, '2026-09').startswith('user:account-b:')
    assert b.session_state['campaigniq_campaign_month'] == '2026-09'
    assert a.session_state['campaigniq_campaign_month'] == '2026-10'


def test_empty_or_stale_chart_selection_leaves_route_unchanged():
    native = ChartUI(); state = {}
    ui = ScopedUI(native, state, 'user:account-a:')
    ui.session_state['campaigniq_primary_view'] = 'Overview'
    render_month_chart(ui, object(), ('2026-10',))
    widget = native.calls[-1]
    for selection in ([], [{'month_key': '2020-01'}]):
        state[widget['key']] = {'selection': {'campaign_month': selection}}
        widget['on_select']()
        assert ui.session_state['campaigniq_primary_view'] == 'Overview'
        assert 'campaigniq_campaign_month' not in ui.session_state
