"""Worker traces stay private and merge only after validated startup succeeds."""
from unittest.mock import patch
import pytest
from campaigniq import navigation_timing as timing
from campaigniq import account_deployment_startup as startup
from tests.test_parallel_account_startup import harness

NAMES={'startup_'+role+'_'+phase for role in ('reader','publisher','preferences') for phase in ('connection','validator','migrations','directory','close')}

def test_normal_startup_records_fifteen_fixed_stages_on_parent_thread(harness):
    trace=timing.Trace(False,True,'Overview');token=timing._current.set(trace)
    try:startup.verify_startup(harness.app)
    finally:timing._current.reset(token)
    assert set(trace.stages)==NAMES|{'startup_concurrent_checks'}
    assert trace.active==[]
    assert all(v['calls']==1 and v['seconds']>=0 for v in trace.stages.values())
    assert trace.authorized is True and trace.view=='Overview'

@pytest.mark.parametrize('failure',[0,1,2,'directory','migrations'])
def test_failed_startup_keeps_connection_cleanup_and_does_not_merge_partial_success(harness,failure):
    if failure=='directory':harness.config['different']=True
    elif failure=='migrations':harness.config['migrations']=[]
    else:harness.config['fail']=failure
    trace=timing.Trace(False);token=timing._current.set(trace)
    try:
        with pytest.raises((ValueError,PermissionError)):startup.verify_startup(harness.app)
    finally:timing._current.reset(token)
    assert set(trace.stages)=={'startup_concurrent_checks'} and trace.active==[]
    assert sum(name=='close' for name,i in harness.events)==3

def test_worker_capture_uses_new_trace_and_restores_context_even_on_failure():
    parent=timing.Trace(True,True,'Data');token=timing._current.set(parent)
    def work():
        child=timing._current.get()
        assert child is not parent and child.authorized is False and child.enabled is False
        with timing.stage('startup_reader_validator'):pass
        return 'unchanged result'
    try:
        result,values=timing.capture_stages(work)
        assert result=='unchanged result' and parent.stages=={}
        assert timing._current.get() is parent
        timing.merge_stages(values);assert parent.stages['startup_reader_validator']['calls']==1
        def fail():raise PermissionError('unchanged failure')
        with pytest.raises(PermissionError):timing.capture_stages(fail)
        assert timing._current.get() is parent
    finally:timing._current.reset(token)

def test_unknown_stage_and_missing_request_trace_are_ignored():
    timing.merge_stages({'unknown':{'calls':1,'seconds':1}})
    parent=timing.Trace(False);token=timing._current.set(parent)
    try:timing.merge_stages({'unknown':{'calls':1,'seconds':1}})
    finally:timing._current.reset(token)
    assert parent.stages=={}

def test_context_manager_exit_receives_original_exception_and_return_value():
    events=[]
    class Manager:
        def __enter__(self):return 'connection'
        def __exit__(self,*error):events.append(error);return True
    with startup._StartupConnectionTiming(Manager(),'startup_reader_') as conn:
        assert conn=='connection'
        raise PermissionError('sentinel')
    assert len(events)==1 and events[0][0] is PermissionError and str(events[0][1])=='sentinel'

def test_failed_enter_never_calls_exit():
    class Manager:
        def __enter__(self):raise PermissionError('enter failed')
        def __exit__(self,*error):raise AssertionError('exit must not run')
    with pytest.raises(PermissionError,match='enter failed'):
        with startup._StartupConnectionTiming(Manager(),'startup_reader_'):pass


def test_timings_are_in_existing_sanitized_monitor_allowlist():
    from collections import deque
    from campaigniq import performance_monitor as monitor
    values={k:{'calls':1,'seconds':0.01} for k in NAMES}
    with patch.object(monitor,'_events',deque(maxlen=2000)),patch.object(monitor._logger,'info'):
        monitor.record('page','Overview',1,stages=values)
        report=monitor.snapshot(administrator=True)
    assert set(report['recent_events'][0]['stages'])==NAMES
