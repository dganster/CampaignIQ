from pathlib import Path
from streamlit.testing.v1 import AppTest


def test_main_dashboard_uses_private_module_instances():
    from campaigniq.ui.account_dashboard_view import report_module
    a,b=report_module(),report_module()
    a.ARTIFACT_STORAGE=object()
    assert a is not b and not hasattr(b,'ARTIFACT_STORAGE')
    import campaigniq.ui.dashboard as shared
    assert not hasattr(shared,'ARTIFACT_STORAGE')


def app_code():
    return '''
import streamlit as st
from types import SimpleNamespace
from campaigniq.account_dashboard import DashboardAccount,DashboardChoices,ReportSnapshot
from campaigniq.account_legacy_results_rehearsal import _candidate
from campaigniq.ui.account_dashboard_view import render
class User:
    def to_dict(self):return {'is_logged_in':True,'iss':'https://issuer.test','sub':'owner'}
class Service:
    store=SimpleNamespace(registry_id='test-registry')
    def choices(self,user):
        return DashboardChoices('owner',False,(
            DashboardAccount('a','First account',True,False),
            DashboardAccount('b','Second account',True,False)))
    def snapshot(self,user,account_id):
        assert user.to_dict()['sub']=='owner'
        return ReportSnapshot(account_id,_candidate('12345678',double=account_id=='b'))
class UI:
    user=User()
    def __getattr__(self,name):return getattr(st,name)
render(UI(),Service(),administration=None)
'''


def test_account_selection_main_reports_and_screen_preferences_remain_separate(monkeypatch):
    import campaigniq.runtime as runtime
    def forbidden(*a,**k):raise AssertionError('Shared runtime must not initialize')
    monkeypatch.setattr(runtime,'build_local_runtime',forbidden)
    app=AppTest.from_string(app_code()).run(timeout=25)
    assert not app.exception
    assert len([r for r in app.radio if r.label=='Navigate'])==1
    assert all('Administration' not in r.options for r in app.radio)
    for view in ('Positions','Campaigns','Performance','Crypto','Overview'):
        next(r for r in app.radio if r.label=='Navigate').set_value(view).run(timeout=25)
        assert not app.exception
    next(c for c in app.checkbox if c.label=='Fit columns to contents').check().run(timeout=25)
    assert not app.exception
    next(s for s in app.selectbox if s.label=='Time zone').select('America/Denver').run(timeout=25)
    assert not app.exception
    next(s for s in app.selectbox if s.label=='Brokerage account').select('b').run(timeout=25)
    assert not app.exception
    assert not next(c for c in app.checkbox if c.label=='Fit columns to contents').value
    assert next(s for s in app.selectbox if s.label=='Time zone').value=='Automatic (browser)'
    next(s for s in app.selectbox if s.label=='Brokerage account').select('a').run(timeout=25)
    assert not app.exception
    assert next(c for c in app.checkbox if c.label=='Fit columns to contents').value
    assert next(s for s in app.selectbox if s.label=='Time zone').value=='America/Denver'
