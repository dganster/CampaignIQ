"""Connection timing preserves SQL order, validators, failure exits and privacy."""
from types import SimpleNamespace
from unittest.mock import patch
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
import pytest
from psycopg.pq import TransactionStatus
from campaigniq import navigation_timing as timing
from campaigniq import account_deployment_startup as startup
from campaigniq.persistence import deployment_account_directory as directory
from tests.test_account_release_startup import stores

@pytest.mark.parametrize('failure',[None,'connect','setup','role','schema','body','transaction'])
def test_real_connection_path_preserves_checks_and_closes_on_failure(failure):
    events=[];store=stores()[0];trace=timing.Trace(False)
    class Conn:
        info=SimpleNamespace(transaction_status=TransactionStatus.INTRANS)
        def __enter__(self):events.append('enter');return self
        def __exit__(self,*error):events.append(('exit',error[0]));return False
        def execute(self,query):
            events.append(query)
            if failure=='setup':raise PermissionError('setup')
    conn=Conn()
    def connect(*a,**kw):
        events.append('connect')
        if failure=='connect':raise PermissionError('connect')
        return conn
    def role(c,target):
        assert c is conn and target is store.target;events.append('role')
        if failure=='role':raise PermissionError('role')
    def schema(c,*,reuse_catalog):
        assert c is conn and reuse_catalog is True;events.append('schema')
        if failure=='schema':raise PermissionError('schema')
    def run():
        with startup._StartupConnectionTiming(store._connection(readonly=True),'startup_reader_') as c:
            events.append('body');assert c is conn
            if failure=='body':raise PermissionError('body')
            if failure=='transaction':conn.info=SimpleNamespace(transaction_status=TransactionStatus.INERROR)
    token=timing._current.set(trace)
    try:
        with patch('psycopg.connect',side_effect=connect),patch.object(directory,'validate_runtime_role',side_effect=role),patch.object(directory,'verify_schema',side_effect=schema):
            if failure:
                with pytest.raises((PermissionError,RuntimeError)):run()
            else:run()
    finally:timing._current.reset(token)
    assert timing._connection_prefix.get() is None
    if failure=='connect':assert 'enter' not in events
    else:assert len([e for e in events if isinstance(e,tuple) and e[0]=='exit'])==1
    if failure is None:
        assert events==['connect','enter','SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY','SET LOCAL search_path=pg_catalog',"SET LOCAL lock_timeout='5s'","SET LOCAL statement_timeout='20s'",'role','schema','body',('exit',None)]
        assert set(trace.stages)=={'startup_reader_'+s for s in ('connection','close','connect','transaction_setup','runtime_role','schema')}
    assert all(k.startswith('startup_reader_') for k in trace.stages)


def test_context_isolated_across_roles_and_reset_after_errors():
    barrier=Barrier(3)
    def run(role):
        def work():
            with timing.connection_timing_scope('startup_'+role+'_'):
                barrier.wait(timeout=3)
                with timing.connection_phase('connect'):pass
            assert timing._connection_prefix.get() is None
        return timing.capture_stages(work)[1]
    with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(run,('reader','publisher','preferences')))
    for role,row in zip(('reader','publisher','preferences'),rows):assert set(row)=={'startup_'+role+'_connect'}
    with pytest.raises(PermissionError):
        with timing.connection_timing_scope('startup_reader_'):raise PermissionError('test')
    assert timing._connection_prefix.get() is None


def test_unknown_context_rejected_and_unscoped_calls_do_not_add_measurements():
    with pytest.raises(ValueError):
        with timing.connection_timing_scope('account-identity'):pass
    trace=timing.Trace(False);token=timing._current.set(trace)
    try:
        with timing.connection_phase('connect'):pass
    finally:timing._current.reset(token)
    assert trace.stages=={}
