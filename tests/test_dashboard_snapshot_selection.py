"""Continuation account choice uses the existing authorized snapshot only."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from campaigniq.account_deployment_dashboard import DeploymentAccountDashboard
from campaigniq import account_deployment_dashboard as module

def harness(*,legacy=False,proof=True,wrong=False,missing=False,failed=False):
    events=[];selected_account=SimpleNamespace(id='selected',legacy=legacy)
    class Artifacts:
        def list_keys(self,**kw):events.append('keys');return ()
        def read_text(self,k):raise AssertionError('unexpected artifact')
    class Session:
        account=SimpleNamespace(account_id='wrong' if wrong else 'selected',storage_scope='scope')
        _known_directory_snapshot=SimpleNamespace(accounts=[] if missing else [selected_account])
        artifacts=Artifacts();retained_rows=()
        drafts=SimpleNamespace(list_drafts=lambda:())
        def _check(self):
            events.append('check')
            if failed:raise RuntimeError('expired or failed')
        def _read_snapshot_reusable(self):events.append('proof');return proof
    class Gateway:
        _continue_existing=True
        @contextmanager
        def transaction(self,user,account_id):
            events.append(('transaction',user,account_id));yield Session();events.append('closed')
    dashboard=object.__new__(DeploymentAccountDashboard);dashboard.gateway=Gateway();dashboard.inputs=SimpleNamespace()
    dashboard.choices=lambda *a:(_ for _ in ()).throw(AssertionError('duplicate choices connection'))
    return dashboard,events

def test_account_is_selected_from_guarded_snapshot_without_extra_choices_connection():
    dashboard,events=harness();result=dashboard.snapshot('user','selected')
    assert result.namespace=='scope' and result.list_keys()==()
    assert events[0]==('transaction','user','selected') and 'proof' in events and events[-1]=='closed'

@pytest.mark.parametrize('kwargs,error',[
    ({'proof':False},ValueError),({'wrong':True},PermissionError),({'missing':True},PermissionError),({'failed':True},RuntimeError)])
def test_invalid_snapshot_cannot_materialize_report(kwargs,error):
    dashboard,events=harness(**kwargs)
    with pytest.raises(error):dashboard.snapshot('user','selected')
    assert 'keys' not in events

def test_foreign_access_is_rejected_by_gateway_before_any_report_read():
    dashboard,events=harness()
    @contextmanager
    def denied(*a):raise PermissionError('foreign');yield
    dashboard.gateway.transaction=denied
    with pytest.raises(PermissionError):dashboard.snapshot('foreign','selected')
    assert events==[]

def test_non_continuation_unknown_selection_keeps_existing_choices_gate():
    dashboard,events=harness();dashboard.gateway._continue_existing=False
    dashboard.choices=lambda user:SimpleNamespace(accounts=[])
    with pytest.raises(PermissionError,match='unavailable'):dashboard.snapshot('user','selected')
    assert events==[]

def test_snapshot_directory_is_fresh_on_next_request():
    dashboard,events=harness();dashboard.snapshot('user','selected')
    denied,new=harness(missing=True);dashboard.gateway=denied.gateway
    with pytest.raises(PermissionError):dashboard.snapshot('user','selected')
    assert 'keys' not in new
