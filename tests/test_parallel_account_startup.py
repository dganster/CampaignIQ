"""Bounded concurrent startup keeps every validation and joins all failures."""
from contextlib import contextmanager,ExitStack
from threading import Barrier,Event,Lock,Thread
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from campaigniq import account_deployment_startup as startup
from campaigniq.account_deployment_application import DeploymentApplication
from tests.test_account_release_startup import stores

@pytest.fixture
def harness():
    app=DeploymentApplication(*stores());events=[];lock=Lock();barrier=Barrier(3)
    config=dict(fail=None,migrations=[(1,),(2,),(3,),(4,)],different=False,barrier=True)
    class Connection:
        def __init__(self,index):self.index=index
        def execute(self,query):
            assert query=='SELECT version FROM campaigniq_accounts.schema_migrations ORDER BY version'
            with lock:events.append(('migrations',self.index))
            return SimpleNamespace(fetchall=lambda:config['migrations'])
    def connection(index):
        @contextmanager
        def opened(*,readonly):
            assert readonly is True
            with lock:events.append(('open',index))
            try:yield Connection(index)
            finally:
                with lock:events.append(('close',index))
        return opened
    def validate(index):
        def checked(conn,*args):
            assert conn.index==index
            assert args==((app.store.target.user,) if index==0 else ())
            with lock:events.append(('validate',index))
            if config['barrier']:barrier.wait(timeout=3)
            if config['fail']==index:raise PermissionError('role rejected')
        return checked
    class Directory:
        def __init__(self,conn,settings):self.index=conn.index
        def _load(self):
            with lock:events.append(('directory',self.index))
            return self.index if config['different'] else 'same immutable directory'
    with ExitStack() as stack:
        for i,store in enumerate((app.store,app.inputs._publisher.writer,app.preferences.store)):stack.enter_context(patch.object(store,'_connection',new=connection(i)))
        for i,name in enumerate(('_verify_complete','validate_publication_role','validate_preference_role')):stack.enter_context(patch.object(startup,name,new=validate(i)))
        stack.enter_context(patch.object(startup,'DirectoryTransaction',Directory))
        yield SimpleNamespace(app=app,events=events,config=config)

def test_all_three_roles_overlap_and_validate_each_connection(harness):
    h=harness;startup.verify_startup(h.app)
    for i in range(3):
        for name in ('open','validate','migrations','directory','close'):assert h.events.count((name,i))==1

@pytest.mark.parametrize('role',[0,1,2])
def test_each_failed_role_closes_every_connection_before_return(harness,role):
    h=harness;h.config['fail']=role
    with pytest.raises(PermissionError,match='role rejected'):startup.verify_startup(h.app)
    assert sorted(i for name,i in h.events if name=='close')==[0,1,2]

def test_directory_mismatch_fails_closed(harness):
    h=harness;h.config['different']=True
    with pytest.raises(ValueError,match='directory changed'):startup.verify_startup(h.app)
    assert sum(name=='close' for name,i in h.events)==3

def test_incomplete_migrations_still_fail(harness):
    h=harness;h.config['migrations']=[(1,),(2,),(3,)]
    with pytest.raises(ValueError,match='migrations'):startup.verify_startup(h.app)
    assert sum(name=='close' for name,i in h.events)==3

def test_outcomes_are_never_cached_between_calls(harness):
    h=harness;startup.verify_startup(h.app);h.config['fail']=2
    with pytest.raises(PermissionError):startup.verify_startup(h.app)
    for i in range(3):assert h.events.count(('validate',i))==2 and h.events.count(('close',i))==2

def test_wrong_application_rejected_without_connecting(harness):
    with pytest.raises(ValueError):startup.verify_startup(object())
    assert harness.events==[]

def test_failure_waits_for_active_peer_before_return(harness):
    h=harness;h.config['barrier']=False;entered=Event();release=Event();finished=Event();errors=[]
    def blocked(conn):entered.set();assert release.wait(timeout=3)
    def failed(conn):assert entered.wait(timeout=3);raise PermissionError('fail while peer active')
    def caller():
        try:startup.verify_startup(h.app)
        except Exception as error:errors.append(error)
        finally:finished.set()
    with patch.object(startup,'validate_preference_role',new=blocked),patch.object(startup,'validate_publication_role',new=failed):
        thread=Thread(target=caller);thread.start()
        try:
            assert entered.wait(timeout=2);assert not finished.wait(timeout=0.05)
        finally:release.set();thread.join(timeout=3)
    assert finished.is_set() and len(errors)==1 and isinstance(errors[0],PermissionError)
    assert sum(name=='close' for name,i in h.events)==3
