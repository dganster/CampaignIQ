import json
from unittest.mock import patch
import pytest
from streamlit.testing.v1 import AppTest
from campaigniq.ui import performance_statistics as view

REPORT=dict(scope='Server only',events_retained=2,oldest_timestamp='2026-11-11T00:00:00+00:00',
    summary=[dict(kind='page',name='Data',profiled=False,count=1,errors=0,p50_seconds=1,p95_seconds=1,max_seconds=1,last_seconds=1),
        dict(kind='page',name='Overview',profiled=False,count=1,errors=0,p50_seconds=5,p95_seconds=5,max_seconds=5,last_seconds=5)],
    stage_summary=[dict(view='Data',stage='report_rendering',profiled=False,count=1,p95_seconds=1,max_seconds=1)],recent_events=[])

def test_main_page_tables_and_download_payload():
    source='''import streamlit as st
from campaigniq.navigation_timing import authorize_panel
class Service:pass
authorize_panel(st,administrator=True)
'''
    with patch.object(view,'snapshot',return_value=REPORT):
        at=AppTest.from_string(source).run()
        at.button[0].click().run()
    assert not at.exception
    assert not len(at.sidebar.dataframe) and len(at.main.dataframe)==2
    assert at.dataframe[0].value.iloc[0]['Page or operation']=='Overview'
    assert len(at.get('download_button'))==1
    assert 'Performance statistics' in [e.label for e in at.expander]

def test_export_is_the_same_sanitized_snapshot():
    class UI:
        def expander(self,*a,**kw):return self
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def caption(self,*a):pass
        def write(self,*a):pass
        def dataframe(self,*a,**kw):pass
        def download_button(self,label,data,**kw):self.data=data;self.options=kw
    ui=UI()
    with patch.object(view,'snapshot',return_value=REPORT):view.render(ui,administrator=True)
    assert json.loads(ui.data)==REPORT
    assert ui.options['mime']=='application/json'

def test_non_admin_stops_before_render_or_export():
    for actor in (False,None,1,'true'):
        with pytest.raises(PermissionError):view.render(object(),administrator=actor)

def test_empty_measurements_show_clear_message():
    with patch.object(view,'snapshot',return_value=dict(REPORT,events_retained=0,summary=[],stage_summary=[])):
        at=AppTest.from_string('''import streamlit as st
from campaigniq.ui.performance_statistics import render
render(st,administrator=True)
''').run()
    assert not at.exception and len(at.info)==1 and len(at.dataframe)==0
