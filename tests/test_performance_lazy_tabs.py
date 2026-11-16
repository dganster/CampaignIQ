from unittest.mock import patch
import pytest
from streamlit.testing.v1 import AppTest
from campaigniq.ui import account_dashboard_view as reports
from tests.test_account_dashboard_view import app_code

@pytest.mark.parametrize('tab,visible,hidden',[
 ('Monthly Results','Monthly Performance',('Underlying Performance','Realized Drawdown')),
 ('Campaign Analysis','Underlying Performance',('Monthly Performance','Realized Drawdown')),
 ('Drawdown','Realized Drawdown',('Monthly Performance','Underlying Performance'))])
def test_only_selected_performance_tab_renders_and_runs_its_calculations(tab,visible,hidden):
    original=reports.report_module;calls=[]
    def module():
        result=original()
        for name in ('summarize_underlying_performance','summarize_period_qualified_realized_drawdown','summarize_maximum_drawdown_contributions'):
            fn=getattr(result,name)
            def measured(*a,_fn=fn,_name=name,**kw):
                calls.append(_name);return _fn(*a,**kw)
            setattr(result,name,measured)
        return result
    source=app_code()
    with patch.object(reports,'report_module',side_effect=module):
        at=AppTest.from_string(source).run(timeout=25)
        at.session_state['account-dashboard:test-registry:owner:a:campaigniq_performance_tab']=tab
        next(r for r in at.radio if r.label=='Navigate').set_value('Performance').run(timeout=25)
    assert not at.exception
    headings=[x.value for x in at.subheader]
    assert visible in headings and not any(x in headings for x in hidden)
    if tab=='Campaign Analysis':assert 'summarize_underlying_performance' in calls
    else:assert 'summarize_underlying_performance' not in calls
    if tab=='Drawdown':assert 'summarize_period_qualified_realized_drawdown' in calls and 'summarize_maximum_drawdown_contributions' in calls
    else:assert 'summarize_period_qualified_realized_drawdown' not in calls and 'summarize_maximum_drawdown_contributions' not in calls

def test_tab_widget_key_is_scoped_to_each_account():
    from campaigniq.ui.account_dashboard_state import ScopedUI
    from types import SimpleNamespace
    seen=[]
    class UI:
        def tabs(self,*a,**kw):seen.append(kw['key']);return []
    state={}
    for prefix in ('owner:account-a:','owner:account-b:'):
        ui=ScopedUI(UI(),state,prefix)
        ui.tabs(['Monthly Results','Drawdown'],key='campaigniq_performance_tab')
    assert seen==['owner:account-a:campaigniq_performance_tab','owner:account-b:campaigniq_performance_tab']

def test_tab_selection_can_change_on_next_render():
    source=app_code()
    at=AppTest.from_string(source).run(timeout=25)
    next(r for r in at.radio if r.label=='Navigate').set_value('Performance').run(timeout=25)
    assert not at.exception and 'Monthly Performance' in [x.value for x in at.subheader]
    for tab,heading in [('Drawdown','Realized Drawdown'),('Campaign Analysis','Underlying Performance'),('Monthly Results','Monthly Performance')]:
        at.session_state['account-dashboard:test-registry:owner:a:campaigniq_performance_tab']=tab
        at.run(timeout=25)
        assert not at.exception and heading in [x.value for x in at.subheader]
