import json
from types import SimpleNamespace
import pytest
from streamlit.testing.v1 import AppTest
from campaigniq.account_dashboard import ReportSnapshot
from campaigniq.account_preferences import SavedPreferences,document
from campaigniq.ui.account_preferences_view import PreferenceStorage
from campaigniq.ui.preferences import DEFAULT_PREFERENCES,preference_key

BACKEND=None


class Backend:
    def __init__(self):self.rows={};self.ready=True
    def load(self,user,account):
        rev,values=self.rows.get(account,(0,{}))
        return SavedPreferences(rev,tuple(values.items()),self.ready)
    def save(self,user,account,values,*,expected_revision):
        old,_=self.rows.get(account,(0,{}))
        if old!=expected_revision:raise ValueError('Preferences changed')
        self.rows[account]=(old+1,{k:v for k,v in document(values).items() if k!='version'})
        return old+1


def test_adapter_preserves_legacy_bytes_and_writes_only_own_preference_key():
    backend=Backend();key=preference_key('owner')
    original=json.dumps({'version':1,**DEFAULT_PREFERENCES,'fit_columns':True})
    snapshot=ReportSnapshot('legacy',[(key,original)])
    adapter=PreferenceStorage(backend,object(),'account','owner',snapshot)
    assert adapter.exists(key) and adapter.read_text(key)==original
    adapter.write_text(key,json.dumps({'version':1,**DEFAULT_PREFERENCES,'timezone':'America/Denver'}))
    assert snapshot.read_text(key)==original and backend.rows['account'][0]==1
    other=PreferenceStorage(backend,object(),'account','owner',snapshot)
    assert json.loads(other.read_text(key))['timezone']=='America/Denver'
    with pytest.raises(PermissionError):adapter.write_text('2026-08-realized-attributions.json',original)
    with pytest.raises(PermissionError):adapter.delete(key)
    with pytest.raises(PermissionError):adapter.initialize()


CODE='''
import streamlit as st
from campaigniq.ui.account_dashboard_state import ScopedUI
from campaigniq.ui.account_preferences_view import render
from campaigniq.account_dashboard import ReportSnapshot
from tests.test_account_preferences_view import BACKEND
account=st.selectbox('Account',['a','b'])
ui=ScopedUI(st,st.session_state,'personal:'+account+':')
render(ui,BACKEND,object(),account,'owner',ReportSnapshot(account,[]))
'''


def test_saved_preferences_survive_browser_session_and_account_switch(monkeypatch):
    backend=Backend();monkeypatch.setattr('tests.test_account_preferences_view.BACKEND',backend)
    app=AppTest.from_string(CODE).run(timeout=20)
    next(c for c in app.checkbox if c.label=='Fit columns to contents').check().run(timeout=20)
    next(c for c in app.selectbox if c.label=='Time zone').select('America/Denver').run(timeout=20)
    assert not app.exception and backend.rows['a'][0]==2
    next(c for c in app.selectbox if c.label=='Account').select('b').run(timeout=20)
    assert not next(c for c in app.checkbox if c.label=='Fit columns to contents').value
    assert not app.exception
    fresh=AppTest.from_string(CODE).run(timeout=20)
    assert not fresh.exception
    assert next(c for c in fresh.checkbox if c.label=='Fit columns to contents').value
    assert next(c for c in fresh.selectbox if c.label=='Time zone').value=='America/Denver'


def test_stale_callback_does_not_overwrite_newer_preferences_and_reload_recovers(monkeypatch):
    backend=Backend();monkeypatch.setattr('tests.test_account_preferences_view.BACKEND',backend)
    app=AppTest.from_string(CODE).run(timeout=20)
    backend.rows['a']=(1,{**DEFAULT_PREFERENCES,'timezone':'America/Denver'})
    next(c for c in app.checkbox if c.label=='Fit columns to contents').check().run(timeout=20)
    assert not app.exception and backend.rows['a'][0]==1
    assert any('could not be saved' in w.value for w in app.warning)
    next(b for b in app.button if b.label=='Reload saved preferences').click().run(timeout=20)
    assert not app.exception
    assert next(c for c in app.selectbox if c.label=='Time zone').value=='America/Denver'
    assert not next(c for c in app.checkbox if c.label=='Fit columns to contents').value


def test_corrupt_personal_preferences_preserve_report_access_without_overwriting(monkeypatch):
    from campaigniq.account_preferences import PreferenceIntegrityError
    backend=Backend()
    def corrupt(*a):raise PreferenceIntegrityError('Bad preference checksum')
    backend.load=corrupt
    monkeypatch.setattr('tests.test_account_preferences_view.BACKEND',backend)
    app=AppTest.from_string(CODE).run(timeout=20)
    assert not app.exception and any('could not be verified' in w.value for w in app.warning)
    next(c for c in app.checkbox if c.label=='Fit columns to contents').check().run(timeout=20)
    assert not app.exception and not backend.rows
