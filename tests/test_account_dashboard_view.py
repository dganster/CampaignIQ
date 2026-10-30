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
    def snapshot(self,user,account_id,*,include_history=False):
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


def test_account_reports_stage_only_matching_originals_and_remove_temp_files(monkeypatch):
    import campaigniq.account_history_evidence as history
    captured=[];materialize=history.materialize_history
    def inspect(root,evidence):
        path=materialize(root,evidence)
        assert len(evidence.originals)==1
        assert (path/'Account Trade History January 2025.csv').read_bytes()==evidence.originals[0].content
        captured.append(path)
        return path
    monkeypatch.setattr(history,'materialize_history',inspect)
    code=app_code().replace("        return ReportSnapshot(account_id,_candidate('12345678',double=account_id=='b'))",'''        from hashlib import sha256
        from campaigniq.account_history_evidence import bind_history
        from campaigniq.account_selection_rehearsal import synthetic_sources
        from campaigniq.import_contract import MonthlyInputRole
        snapshot=ReportSnapshot(account_id,_candidate('12345678',double=account_id=='b'))
        raw=synthetic_sources('12345678',double=account_id=='b')[MonthlyInputRole.THINKORSWIM_TRADE_HISTORY].getvalue()
        snapshot.history=bind_history(snapshot,[('source',sha256(raw).hexdigest(),raw)])
        return snapshot''')
    app=AppTest.from_string(code).run(timeout=25)
    assert not app.exception and captured and all(not p.exists() for p in captured)
    assert any('verified for 1 of 1' in caption.value for caption in app.caption)
    next(r for r in app.radio if r.label=='Navigate').set_value('Positions').run(timeout=25)
    assert not app.exception and all(not p.exists() for p in captured)
