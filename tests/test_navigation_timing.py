from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import json
from unittest.mock import patch
import pytest
from campaigniq import navigation_timing as timing
class UI:
    def __init__(self,enabled=True):
        self.session_state={timing.KEY:enabled};self.sidebar=self;self.reports=[];self.checkboxes=[]
    def expander(self,*a,**kw):return nullcontext()
    def checkbox(self,*a,**kw):self.checkboxes.append(kw['key'])
    def caption(self,*a):pass
    def json(self,value):self.reports.append(value)

def test_disabled_trace_does_not_start_profiler_or_render_report():
    ui=UI(False)
    with patch.object(timing.cProfile,'Profile',side_effect=AssertionError('Must remain off')):
        with timing.navigation_run(ui):
            timing.authorize_panel(ui,administrator=True)
            with timing.stage('report_rendering'):pass
    assert not ui.reports

def test_authorized_actual_page_report_contains_only_timings():
    ui=UI();secret='PRIVATE-IDENTITY-AND-DSN'
    with timing.navigation_run(ui):
        timing.authorize_panel(ui,administrator=True);timing.select_view('Performance')
        with timing.stage('report_rendering'):
            with timing.stage('preference_load_and_authorization'):assert secret
        with timing.stage(secret):pass
    assert len(ui.reports)==1 and ui.reports[0]['view']=='Performance'
    assert ui.reports[0]['stages']['report_rendering']['calls']==1
    assert ui.reports[0]['stages']['preference_load_and_authorization']['calls']==1
    assert secret not in json.dumps(ui.reports)

@pytest.mark.parametrize('administrator',[False,None,1,'true'])
def test_profile_flag_cannot_grant_administrator_visibility(administrator):
    ui=UI()
    with timing.navigation_run(ui):
        timing.authorize_panel(ui,administrator=administrator)
        with timing.stage('report_rendering'):pass
    assert not ui.reports and not ui.checkboxes

def test_trace_resets_authorization_for_each_navigation():
    ui=UI()
    with timing.navigation_run(ui):timing.authorize_panel(ui,administrator=True)
    with timing.navigation_run(ui):timing.authorize_panel(ui,administrator=False)
    assert len(ui.reports)==1 and timing._current.get() is None

def test_stop_or_rerun_restores_context_without_appending_output():
    ui=UI()
    with pytest.raises(RuntimeError):
        with timing.navigation_run(ui):
            timing.authorize_panel(ui,administrator=True)
            with timing.stage('report_rendering'):raise RuntimeError('stop')
    assert not ui.reports and timing._current.get() is None

def test_concurrent_sessions_keep_separate_stage_counts():
    def worker(count):
        ui=UI()
        with timing.navigation_run(ui):
            timing.authorize_panel(ui,administrator=True)
            for _ in range(count):
                with timing.stage('report_rendering'):pass
        return ui.reports[0]['stages']['report_rendering']['calls']
    with ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(worker,[2,5]))==[2,5]

def test_real_streamlit_stop_still_displays_profile_and_stops_rendering():
    from streamlit.testing.v1 import AppTest
    source='''import streamlit as st
from campaigniq.navigation_timing import navigation_run,authorize_panel,stage,timed_ui,KEY
st.session_state[KEY]=True
with navigation_run(st):
    authorize_panel(st,administrator=True)
    with stage('report_rendering'):
        st.write('before stop')
        timed_ui(st).stop()
    st.write('must never render')
'''
    at=AppTest.from_string(source).run()
    assert not at.exception and len(at.json)==1
    report=json.loads(at.json[0].value)
    assert report['stages']['report_rendering']['calls']==1
    assert not any('must never render' in x.value for x in at.markdown)
