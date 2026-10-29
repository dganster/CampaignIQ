import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

from tests.test_dashboard_sources import storage

DASHBOARD=Path(__file__).parents[1]/'src/campaigniq/ui/dashboard.py'


def fresh():
    spec=importlib.util.spec_from_file_location('campaigniq_dashboard_boundary_test',DASHBOARD)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_import_never_authenticates_builds_runtime_opens_storage_or_records_visit(monkeypatch,tmp_path):
    import campaigniq.runtime as runtime
    import campaigniq.ui.access_history as history
    import campaigniq.ui.oidc_access_gate as oidc
    import streamlit as st
    def forbidden(*a,**k):raise AssertionError('Startup executed during import')
    monkeypatch.setattr(runtime,'build_local_runtime',forbidden)
    monkeypatch.setattr(history,'access_history_storage',forbidden)
    monkeypatch.setattr(history,'record_session_access',forbidden)
    monkeypatch.setattr(oidc,'authorized_streamlit_access',forbidden)
    monkeypatch.setattr(st,'stop',forbidden)
    monkeypatch.setenv('CAMPAIGNIQ_DATA_ROOT',str(tmp_path/'untouched'))
    monkeypatch.setenv('CAMPAIGNIQ_AUTH_MODE','oidc')
    monkeypatch.setenv('CAMPAIGNIQ_STORAGE_BACKEND','postgres')
    monkeypatch.setenv('CAMPAIGNIQ_DEV_DATABASE_URL','invalid-settings-must-not-be-used')
    module=fresh()
    assert callable(module.initialize_dashboard) and callable(module.render_dashboard)
    assert not hasattr(module,'RUNTIME') and not hasattr(module,'ARTIFACT_STORAGE')
    assert not (tmp_path/'untouched').exists()


def test_explicit_startup_authenticates_before_runtime_and_access_history(monkeypatch,tmp_path):
    import campaigniq.local_import_workspace as routing
    module=fresh();calls=[]
    access=SimpleNamespace(workspace_id='workspace-owner')
    runtime=SimpleNamespace(authoritative_state_root=tmp_path/'state',historical_source_root=tmp_path/'history',artifact_storage=object())
    monkeypatch.setattr(module,'require_dashboard_access',lambda:(calls.append('authorize') or access))
    monkeypatch.setattr(routing,'resolve_local_import_workspace',lambda **kw:(calls.append(('workspace',kw['access_workspace'])) or 'workspace-owner'))
    monkeypatch.setattr(module,'build_local_runtime',lambda **kw:(calls.append(('runtime',kw['workspace_id'])) or runtime))
    monkeypatch.setattr(module,'access_history_storage',lambda root:(calls.append('history') or object()))
    monkeypatch.setattr(module,'record_session_access',lambda *a:(calls.append('visit') or None))
    monkeypatch.setattr(module,'st',SimpleNamespace(user=SimpleNamespace(to_dict=lambda:{}),session_state={}))
    module.initialize_dashboard()
    assert calls==['authorize',('workspace','workspace-owner'),('runtime','workspace-owner'),'history','visit']
    assert module.RUNTIME is runtime and module.ARTIFACT_STORAGE is runtime.artifact_storage
    assert module.HISTORICAL_SOURCE_ROOT==tmp_path/'history'


def test_denied_startup_never_constructs_runtime(monkeypatch):
    module=fresh()
    def denied():raise PermissionError('Denied')
    def forbidden(**kw):raise AssertionError('Runtime before authorization')
    monkeypatch.setattr(module,'require_dashboard_access',denied)
    monkeypatch.setattr(module,'build_local_runtime',forbidden)
    with pytest.raises(PermissionError):module.initialize_dashboard()
    assert not hasattr(module,'ARTIFACT_STORAGE')


def test_legacy_report_wrapper_reads_shared_sources_without_runtime(monkeypatch):
    module=fresh();store=storage()
    monkeypatch.setattr(module,'ARTIFACT_STORAGE',store,raising=False)
    monkeypatch.setattr(module,'AUTHORITATIVE_STATE_DIR',Path('unused'),raising=False)
    from campaigniq.dashboard_sources import load_dashboard_sources
    assert module.load_summaries()==load_dashboard_sources(store)
    assert not hasattr(module,'RUNTIME')


def test_renderer_runs_existing_overview_positions_and_data_pages(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    from campaigniq.account_legacy_results_rehearsal import _candidate
    root=tmp_path/'state';root.mkdir()
    for key,text in _candidate('12345678').items():(root/key).write_text(text)
    monkeypatch.setenv('CAMPAIGNIQ_STORAGE_BACKEND','filesystem')
    code='''
from pathlib import Path
from types import SimpleNamespace
import importlib.util
spec=importlib.util.spec_from_file_location('campaigniq_dashboard_smoke',DASHBOARD_PATH)
dashboard=importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)
from campaigniq.persistence.artifact_storage import LocalFilesystemArtifactStorage
import streamlit as st
root=Path(ROOT)
dashboard.PROJECT_ROOT=root.parent
dashboard.require_dashboard_access=lambda:None
dashboard.build_local_runtime=lambda **kw:SimpleNamespace(artifact_storage=LocalFilesystemArtifactStorage(root),
    authoritative_state_root=root,historical_source_root=root.parent/'history')
dashboard.access_history_storage=lambda root:LocalFilesystemArtifactStorage(root/'visits')
dashboard.record_session_access=lambda *a:None
dashboard.initialize_dashboard()
dashboard.render_dashboard()
'''.replace('DASHBOARD_PATH',repr(str(DASHBOARD))).replace('root=Path(ROOT)','root=Path('+repr(str(root))+')')
    app=AppTest.from_string(code).run(timeout=20)
    assert not app.exception
    for label in ('Positions','Data'):
        next(r for r in app.radio if r.label=='Navigate').set_value(label).run(timeout=20)
        assert not app.exception


def test_normal_streamlit_file_entry_still_starts_the_main_dashboard(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    from campaigniq.account_legacy_results_rehearsal import _candidate
    from campaigniq.persistence.artifact_storage import LocalFilesystemArtifactStorage
    import campaigniq.runtime as runtime
    import campaigniq.ui.access_gate as gate
    import campaigniq.ui.access_history as history
    root=tmp_path/'state';root.mkdir()
    for key,text in _candidate('12345678').items():(root/key).write_text(text)
    calls=[]
    monkeypatch.setenv('CAMPAIGNIQ_STORAGE_BACKEND','filesystem')
    monkeypatch.setattr(gate,'authentication_mode',lambda:'password')
    monkeypatch.setattr(gate,'access_password',lambda:None)
    def build(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(artifact_storage=LocalFilesystemArtifactStorage(root),
            authoritative_state_root=root,historical_source_root=tmp_path/'history')
    monkeypatch.setattr(runtime,'build_local_runtime',build)
    monkeypatch.setattr(history,'access_history_storage',lambda project_root:LocalFilesystemArtifactStorage(tmp_path/'visits'))
    monkeypatch.setattr(history,'record_session_access',lambda *a:None)
    app=AppTest.from_file(str(DASHBOARD)).run(timeout=20)
    assert not app.exception and len(calls)==1
    assert any(r.label=='Navigate' for r in app.sidebar.radio)
    assert any(t.value=='CampaignIQ' for t in app.title)
