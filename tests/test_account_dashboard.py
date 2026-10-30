from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from campaigniq.account_dashboard import LocalAccountDashboard, ReportSnapshot
from campaigniq.ui.account_dashboard_state import ScopedState, SessionRoutes, ScopedUI
from tests.test_account_legacy_access import harness, Result, A, B
from tests.test_account_local_publication import local, publish, month, bound_context, context


def test_snapshot_detaches_bytes_and_rejects_mutations():
    rows={'a.json':'original','b.txt':'other'}
    snapshot=ReportSnapshot('account',rows)
    rows['a.json']='changed'
    assert snapshot.read_text('a.json')=='original'
    assert snapshot.list_keys(prefix='a',suffix='.json')==('a.json',)
    for method in ('write_text','delete','initialize'):
        with pytest.raises(PermissionError):
            getattr(snapshot,method)(*(() if method=='initialize' else ('a','x')))


def test_scoped_state_and_routes_do_not_cross_accounts_or_users():
    raw={};a=ScopedState(raw,'user1:account1:');b=ScopedState(raw,'user1:account2:');c=ScopedState(raw,'user2:account1:')
    a['filter']='SPY';b['filter']='AAPL'
    SessionRoutes(a).from_dict({'view':'Positions','symbol':'SPY'})
    assert b['filter']=='AAPL' and not list(c) and not SessionRoutes(b)
    assert SessionRoutes(a).to_dict()=={'view':'Positions','symbol':'SPY'}
    del a['filter']
    assert b['filter']=='AAPL'


def test_widget_explicit_implicit_and_form_keys_are_scoped():
    raw={};calls=[]
    class UI:
        def __getattr__(self,name):
            return lambda *a,**k:calls.append((name,a,k))
    ui=ScopedUI(UI(),raw,'a:')
    ui.selectbox('month',['Jan'],key='month');ui.checkbox('flag');ui.form('upload')
    assert [c[2]['key'] for c in calls][0]=='a:month'
    assert calls[1][2]['key'].startswith('a:checkbox:')
    assert calls[2][1]==() and calls[2][2]['key']=='a:upload'
    ui.session_state['month']='Jan'
    assert raw['a:month']=='Jan'


def test_guarded_directory_constructor_rejects_alternate_storage():
    with pytest.raises(ValueError):LocalAccountDashboard(object(),trusted_issuer='https://issuer.test',authorization={})


def test_legacy_snapshot_current_signin_namespace_parity_and_no_writes(harness,local):
    h=harness;publish(local);rows=dict(local.artifacts[local.account].rows)
    original=h.conn.execute
    def execute(sql,params=()):
        q=' '.join(sql.split())
        if q.startswith('SELECT artifact_key,content'):
            assert params==('workspace-a',)
            return Result(sorted(rows.items()))
        if q.startswith('SELECT artifact_key FROM'):return Result([(k,) for k in sorted(rows)])
        if q.startswith('SELECT content FROM'):return Result([(rows[params[1]],)])
        return original(sql,params)
    h.conn.execute=execute
    service=object.__new__(LocalAccountDashboard);service.legacy=h.reader
    result=service.snapshot(h.user,h.account)
    assert result.namespace=='workspace-a' and result.read_text('2025-01-lot-book.json')==rows['2025-01-lot-book.json']
    assert all(h.modes)
    assert not any(q.startswith(('UPDATE','INSERT','DELETE','CREATE')) for q,_ in h.conn.queries)
    assert result.read_text('2025-01-finalized.json')


def test_legacy_snapshot_refuses_foreign_account_before_returning_bytes(harness):
    h=harness;service=object.__new__(LocalAccountDashboard);service.legacy=h.reader
    other=h.other
    with pytest.raises(PermissionError):service.snapshot(h.user,other)


from tests.test_account_signedin_results import results, signed, FIXTURES


@pytest.fixture
def new_dashboard(results,monkeypatch):
    from campaigniq import account_dashboard as module
    from campaigniq.account_signin import signed_in_accounts
    c=results
    from dataclasses import replace
    c.directory=replace(c.directory, accounts=tuple(replace(a,legacy=False,broker="schwab") if a.id==c.account else a for a in c.directory.accounts))
    service=object.__new__(LocalAccountDashboard)
    service.inputs=c.signed;service.store=c.signed.gateway._directory
    @contextmanager
    def authorized(user):
        yield None,c.directory,signed_in_accounts(user,c.directory,trusted_issuer=c.admin.issuer)
    service.legacy=SimpleNamespace(_authorized=authorized)
    from campaigniq import account_signedin_results as existing
    monkeypatch.setattr(module,'AccountDataSession',existing.AccountDataSession)
    c.dashboard=service
    return c


def test_new_account_snapshot_validates_receipts_inside_one_readonly_snapshot(new_dashboard):
    c=new_dashboard;before=dict(c.artifacts[c.account].rows)
    report=c.dashboard.snapshot(c.user,c.account)
    assert report.read_text('2025-01-lot-book.json')==before['2025-01-lot-book.json']
    assert c.snapshots==[True] and all(not s._active for s in c.read_sessions)
    assert c.artifacts[c.account].rows==before


@pytest.mark.parametrize('corruption',['receipt','original','orphan'])
def test_new_account_invalid_source_returns_no_snapshot(new_dashboard,corruption):
    c=new_dashboard
    import json
    if corruption=='receipt':
        key='2025-01-account-publication-receipt.json'
        value=json.loads(c.artifacts[c.account].rows[key]);value['draft_revision']=99
        c.artifacts[c.account].rows[key]=json.dumps(value)
    elif corruption=='original':
        repo=c.repositories[c.account];key=(c.draft.id,'thinkorswim_trade_history')
        name,media,raw=repo.docs[key];repo.docs[key]=(name,media,raw+b'changed')
    else:c.artifacts[c.account].rows['2025-02-finalized.json']='{}'
    with pytest.raises(ValueError):c.dashboard.snapshot(c.user,c.account)
    assert all(not s._active for s in c.read_sessions)


def test_scoped_values_survive_widget_cleanup_without_restoring_trigger_or_upload_state():
    raw={};a=ScopedState(raw,'a:');b=ScopedState(raw,'b:')
    a['range']='2025-01';b['range']='2026-09'
    del raw['a:range']  # Streamlit's hidden-widget cleanup, not a user deletion.
    assert a['range']=='2025-01' and b['range']=='2026-09'
    calls=[]
    class UI:
        def checkbox(self,*args,**kwargs):
            calls.append(raw.get(kwargs['key']))
            return raw.get(kwargs['key'],False)
        def button(self,*args,**kwargs):return True
        def file_uploader(self,*args,**kwargs):return b'not a screen preference'
    a['fit']=True;del raw['a:fit']
    ui=ScopedUI(UI(),raw,'a:')
    assert ui.checkbox('fit',key='fit') is True and calls==[True]
    ui.button('publish',key='publish');ui.file_uploader('source',key='source')
    assert 'publish' not in a.saved and 'source' not in a.saved
    del a['range']
    with pytest.raises(KeyError):a['range']


def test_history_originals_join_the_new_account_readonly_snapshot(new_dashboard,monkeypatch):
    from hashlib import sha256
    from campaigniq import account_history_evidence as history
    c=new_dashboard
    original=c.repositories[c.account].docs[(c.draft.id,'thinkorswim_trade_history')][2]
    observations=[]
    def rows(conn,scope,*,legacy):
        observations.append((conn,scope,legacy))
        assert conn is c.read_sessions[-1]._connection
        return [('source',sha256(original).hexdigest(),original)]
    monkeypatch.setattr(history,'retained_history_rows',rows)
    snapshot=c.dashboard.snapshot(c.user,c.account,include_history=True)
    assert snapshot.history.originals[0].content==original and not snapshot.history.gaps
    assert len(observations)==1 and observations[0][2] is False
    assert c.snapshots==[True] and all(not x._active for x in c.read_sessions)
