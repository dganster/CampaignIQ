"""Opt-in per-thread navigation timings; no identities, arguments or result caching."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass,field
from pathlib import Path
from time import perf_counter
import cProfile
import pstats

KEY='campaigniq_navigation_timing_enabled'
_current=ContextVar('campaigniq_navigation_timing',default=None)
_STAGES=frozenset(('release_and_composition','startup_role_and_schema_checks','signed_in_application','account_choices_and_authorization','dashboard_module_import','history_materialization','report_rendering','authorized_account_snapshot','preference_load_and_authorization','preference_controls'))
@dataclass
class Trace:
    enabled:bool
    authorized:bool=False
    view:str=''
    stages:dict=field(default_factory=dict)
    active:list=field(default_factory=list)

@contextmanager
def stage(name):
    trace=_current.get()
    if trace is None or name not in _STAGES:
        yield;return
    start=perf_counter();entry=(name,start);trace.active.append(entry)
    try:yield
    finally:
        trace.active.remove(entry)
        value=trace.stages.setdefault(name,{'calls':0,'seconds':0.0})
        value['calls']+=1;value['seconds']+=perf_counter()-start

def authorize_panel(st,*,administrator):
    trace=_current.get()
    if trace is not None:trace.authorized=administrator is True
    if administrator is True:
        with st.sidebar.expander('Navigation diagnostics'):
            st.checkbox('Measure page loading',value=False,key=KEY)
            st.caption('Automatic lightweight timings are always collected. Enable detailed profiling only when investigating a slow page; profiling adds overhead.')
            if hasattr(st,'button') and st.button('Show performance statistics',key='performance-statistics'):
                from campaigniq.performance_monitor import snapshot
                st.json(snapshot(administrator=administrator))

def select_view(view):
    trace=_current.get()
    if trace is not None and view in ('Overview','Campaigns','Performance','Positions','Data','Crypto','Compare accounts','Administration'):
        trace.view=view

def summarize(trace,profile,total):
    rows=[];sql_calls=0;connect_calls=0;connect_seconds=0.0
    for (filename,line,name),(primitive,calls,self_time,cumulative,callers) in pstats.Stats(profile).stats.items():
        if 'psycopg' in filename and Path(filename).name=='connection.py':
            if name=='execute':sql_calls+=calls
            if name=='connect':connect_calls+=calls;connect_seconds+=cumulative
        if 'campaigniq' in filename or 'psycopg' in filename:
            rows.append(dict(module=Path(filename).name,function=name,calls=calls,seconds=round(cumulative,4)))
    rows.sort(key=lambda x:x['seconds'],reverse=True)
    return dict(view=trace.view,page_seconds=round(total,4),database_calls=sql_calls,new_connections=connect_calls,
        connection_seconds=round(connect_seconds,4),stages={k:dict(calls=v['calls'],seconds=round(v['seconds'],4)) for k,v in trace.stages.items()},
        slowest_functions=rows[:18],scope='Actual signed-in server page run; nested times overlap. Includes diagnostic overhead, excludes browser rendering. No arguments, identities or financial values collected.')

def finish_navigation():
    trace=_current.get()
    if trace is None or not trace.enabled or not trace.authorized or getattr(trace,'_finished',False):return
    trace._finished=True;trace._profile.disable()
    now=perf_counter()
    # Report screens intentionally end with st.stop(). Snapshot active timers
    # before that control signal, without changing or swallowing the stop.
    snapshot=Trace(True,True,trace.view,{k:dict(v) for k,v in trace.stages.items()})
    for name,start in trace.active:
        item=snapshot.stages.setdefault(name,{'calls':0,'seconds':0.0})
        item['calls']+=1;item['seconds']+=now-start
    report=summarize(snapshot,trace._profile,now-trace._start)
    with trace._ui.expander('Navigation timing — this page load',expanded=True):
        trace._ui.caption(report['scope']);trace._ui.json(report)

class _TimingUI:
    def __init__(self,ui):self._ui=ui
    def __getattr__(self,name):return getattr(self._ui,name)
    def __enter__(self):self._ui.__enter__();return self
    def __exit__(self,*a):return self._ui.__exit__(*a)
    def stop(self):
        finish_navigation()
        return self._ui.stop()

def timed_ui(ui):
    trace=_current.get()
    return _TimingUI(ui) if trace is not None else ui

@contextmanager
def navigation_run(st):
    trace=Trace(st.session_state.get(KEY) is True);token=_current.set(trace)
    profile=cProfile.Profile() if trace.enabled else None
    trace._profile=profile;trace._ui=st;trace._start=perf_counter();completed=False
    if profile is not None:profile.enable()
    try:
        yield trace
        completed=True
        finish_navigation()
    except BaseException as error:
        outcome='rerun' if type(error).__name__=='RerunException' else ('stopped' if type(error).__name__=='StopException' else 'error')
        raise
    finally:
        if profile is not None:profile.disable()
        now=perf_counter()
        stages={k:dict(v) for k,v in trace.stages.items()}
        for name,start in trace.active:
            item=stages.setdefault(name,{'calls':0,'seconds':0.0})
            item['calls']+=1;item['seconds']+=now-start
        _current.reset(token)
        from campaigniq.performance_monitor import record
        record('page',trace.view or 'Startup',now-trace._start,outcome=locals().get('outcome','success'),stages=stages,profiled=trace.enabled)
