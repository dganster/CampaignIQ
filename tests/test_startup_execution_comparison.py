"""Both execution schedules keep the same checks; only admin diagnostics offers a control."""
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from campaigniq import navigation_timing as timing,account_deployment_startup as startup
from tests.test_parallel_account_startup import harness

@pytest.mark.parametrize('execution',['sequential','concurrent'])
def test_both_modes_validate_three_roles_and_emit_distinct_total(execution,harness):
    harness.config['barrier']=execution=='concurrent'
    trace=timing.Trace(False);token=timing._current.set(trace)
    try:startup.verify_startup(harness.app,execution=execution)
    finally:timing._current.reset(token)
    for i in range(3):
        for event in ('open','validate','migrations','directory','close'):assert harness.events.count((event,i))==1
    if execution=='sequential':
        assert [e for e in harness.events if e[0] in ('open','close')]==[(e,i) for i in range(3) for e in ('open','close')]
    assert trace.stages['startup_'+execution+'_checks']['calls']==1
    assert 'startup_'+('concurrent' if execution=='sequential' else 'sequential')+'_checks' not in trace.stages

@pytest.mark.parametrize('role',[0,1,2])
def test_sequential_failure_still_closes_and_checks_all_three_without_merging_partial_results(role,harness):
    harness.config.update(barrier=False,fail=role);trace=timing.Trace(False);token=timing._current.set(trace)
    try:
        with pytest.raises(PermissionError):startup.verify_startup(harness.app,execution='sequential')
    finally:timing._current.reset(token)
    assert [i for name,i in harness.events if name=='close']==[0,1,2]
    assert set(trace.stages)=={'startup_sequential_checks'}

@pytest.mark.parametrize('value',[None,True,'skip','SEQUENTIAL',0])
def test_unknown_mode_rejected_without_connections(value,harness):
    with pytest.raises(ValueError):startup.verify_startup(harness.app,execution=value)
    assert harness.events==[]

@pytest.mark.parametrize('bad',['directory','migrations'])
def test_sequential_integrity_conflict_fails_closed(bad,harness):
    harness.config['barrier']=False
    if bad=='directory':harness.config['different']=True
    else:harness.config['migrations']=[]
    with pytest.raises(ValueError):startup.verify_startup(harness.app,execution='sequential')
    assert sum(name=='close' for name,i in harness.events)==3

def test_default_and_explicit_session_selection_do_not_cache():
    state={};assert timing.startup_execution_mode(state)=='concurrent'
    state[timing.STARTUP_MODE_KEY]='sequential';assert timing.startup_execution_mode(state)=='sequential'
    state[timing.STARTUP_MODE_KEY]='concurrent';assert timing.startup_execution_mode(state)=='concurrent'
    state[timing.STARTUP_MODE_KEY]='skip'
    with pytest.raises(ValueError):timing.startup_execution_mode(state)

def test_comparison_control_is_only_rendered_for_administrators():
    class UI:
        sidebar=None
        def __init__(self):self.sidebar=self;self.selections=[]
        def expander(self,*a,**k):return self
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def checkbox(self,*a,**k):pass
        def caption(self,*a):pass
        def selectbox(self,*a,**k):self.selections.append((a,k))
    ui=UI();timing.authorize_panel(ui,administrator=False);assert ui.selections==[]
    timing.authorize_panel(ui,administrator=True)
    assert len(ui.selections)==1 and ui.selections[0][1]['key']==timing.STARTUP_MODE_KEY
    assert ui.selections[0][0][1]==('concurrent','sequential')
