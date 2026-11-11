import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import pytest
from campaigniq import performance_monitor as m
from campaigniq import navigation_timing as t
from tests.test_navigation_timing import UI

@pytest.fixture(autouse=True)
def clean():
    with m._lock:m._events.clear()
    yield
    with m._lock:m._events.clear()

def test_automatic_without_profiler_and_strict_admin_read():
    with patch.object(t.cProfile,'Profile',side_effect=AssertionError('Profiler off')):
        with t.navigation_run(UI(False)):
            t.select_view('Data')
            with t.stage('report_rendering'):pass
    report=m.snapshot(administrator=True)
    assert report['events_retained']==1
    assert report['recent_events'][0]['stages']['report_rendering']['calls']==1
    for actor in (False,None,1,'true'):
        with pytest.raises(PermissionError):m.snapshot(administrator=actor)

def test_proxy_preserves_arguments_result_error_and_excludes_secrets():
    secret='SECRET-ACCOUNT-SQL-DSN'
    class Service:
        untouched=object()
        def publish(self,*args,**kwargs):
            assert args==(secret,) and kwargs=={'receipt':secret}
            return self.untouched
        def no_trading_preview(self,*args):raise ValueError(secret)
    service=Service();proxy=m.TimedService(service)
    assert proxy.publish(secret,receipt=secret) is service.untouched
    assert proxy.untouched is service.untouched
    with pytest.raises(ValueError,match=secret):proxy.no_trading_preview(secret)
    report=m.snapshot(administrator=True)
    assert secret not in json.dumps(report)
    assert sum(r['errors'] for r in report['summary'])==1

def test_thread_safety_bounded_retention_and_percentiles():
    def emit(i):m.record('operation','publish',i/1000)
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(emit,range(2100)))
    report=m.snapshot(administrator=True)
    assert report['events_retained']==2000 and len(report['recent_events'])==50
    row=report['summary'][0]
    assert row['p50_seconds']<=row['p95_seconds']<=row['max_seconds']

def test_logging_failure_does_not_change_completed_action():
    with patch.object(m._logger,'info',side_effect=RuntimeError('log failure')):
        with m.operation('publish'):pass
    assert m.snapshot(administrator=True)['events_retained']==1

def test_unknown_names_never_collected():
    m.record('page','secret',1);m.record('operation','secret',1)
    assert m.snapshot(administrator=True)['events_retained']==0

def test_failed_navigation_cleans_context_and_captures_sanitized_outcome():
    with pytest.raises(ValueError):
        with t.navigation_run(UI(False)):
            t.select_view('Data')
            raise ValueError('secret')
    assert t._current.get() is None
    assert m.snapshot(administrator=True)['recent_events'][0]['outcome']=='error'

def test_actual_stop_is_collected_once_without_profiling():
    from streamlit.testing.v1 import AppTest
    at=AppTest.from_string('''import streamlit as st
from campaigniq.navigation_timing import navigation_run,select_view,timed_ui
with navigation_run(st):
    select_view('Data')
    timed_ui(st).stop()
    st.error('unreachable')
''').run()
    assert not at.exception and not at.error
    report=m.snapshot(administrator=True)
    assert report['events_retained']==1
    assert report['recent_events'][0]['outcome']=='stopped'
    assert report['recent_events'][0]['profiled'] is False

def test_automatic_panel_is_admin_only_and_profiler_samples_separate():
    class Panel(UI):
        def button(self,*a,**kw):return True
    ui=Panel(False)
    with t.navigation_run(ui):t.authorize_panel(ui,administrator=True)
    assert len(ui.reports)==1 and 'summary' in ui.reports[0]
    ui=Panel(False)
    with t.navigation_run(ui):t.authorize_panel(ui,administrator=False)
    assert not ui.reports
    m.record('page','Data',1,profiled=False);m.record('page','Data',5,profiled=True)
    rows=[r for r in m.snapshot(administrator=True)['summary'] if r['name']=='Data']
    assert len(rows)==2
