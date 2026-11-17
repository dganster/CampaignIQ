"""Server boot warms code only and preserves the existing per-request boundary."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json
import os
import subprocess
import sys
import pytest
from campaigniq import account_server_boot as boot
from campaigniq import account_deployment_startup as startup
from campaigniq import account_release_config as release

REPO=Path(__file__).resolve().parents[1]


@pytest.fixture
def setup(monkeypatch):
    order=[]
    config=object()
    monkeypatch.setenv('CAMPAIGNIQ_ACCOUNT_RELEASE_FILE','/private/release.private.json')
    monkeypatch.setenv('CAMPAIGNIQ_ACCOUNT_RELEASE_SHA256','a'*64)
    monkeypatch.setenv('PORT','9876')
    monkeypatch.setattr(sys,'argv',['boot','--repo',str(REPO)])
    def read(path,**kw):
        assert path=='/private/release.private.json' and kw==dict(reviewed_sha256='a'*64)
        order.append('read');return config
    def verify(c,root):
        assert c is config and root==REPO
        order.append('verify')
    monkeypatch.setattr(startup,'read_release',read)
    monkeypatch.setattr(release,'verify_source',verify)
    monkeypatch.setattr(boot,'preload_dependencies',lambda:order.append('preload'))
    def run(name,**kw):
        assert order==['read','verify','preload']
        assert name=='streamlit' and kw==dict(run_name='__main__',alter_sys=True)
        assert sys.argv==['streamlit','run',str(REPO/'src/campaigniq/ui/account_deployment_entrypoint.py'),
                          '--server.address=0.0.0.0','--server.port=9876','--server.headless=true']
        order.append('serve')
    monkeypatch.setattr(boot.runpy,'run_module',run)
    return order


def test_verified_preload_finishes_before_server_cli_and_logs_only_duration(setup,capsys):
    boot.main()
    assert setup==['read','verify','preload','serve']
    log=json.loads(capsys.readouterr().out)
    assert set(log)=={'event','dependency_preload_seconds'}
    assert log['event']=='campaigniq.server_boot.v1' and log['dependency_preload_seconds']>=0


def test_boot_check_only_runs_preload_without_serving(setup,monkeypatch):
    monkeypatch.setattr(sys,'argv',['boot','--repo',str(REPO),'--check-only'])
    boot.main();assert setup==['read','verify','preload']


@pytest.mark.parametrize('cause',['source','preload','port','arguments'])
def test_boot_rejects_failure_before_serving(setup,monkeypatch,cause):
    if cause=='source':monkeypatch.setattr(release,'verify_source',Mock(side_effect=ValueError('source changed')))
    elif cause=='preload':monkeypatch.setattr(boot,'preload_dependencies',Mock(side_effect=ImportError('dependency unavailable')))
    elif cause=='port':monkeypatch.setenv('PORT','70000')
    else:monkeypatch.setattr(sys,'argv',['boot','unreviewed.py'])
    with pytest.raises((ValueError,ImportError,SystemExit)):boot.main()
    assert 'serve' not in setup


def test_real_preload_reads_no_account_data_and_retains_no_private_dashboard(tmp_path):
    code='''
import socket,sys
from unittest.mock import patch
from campaigniq import account_server_boot as boot
from campaigniq.ui.account_dashboard_view import report_module
from campaigniq.ui import dashboard
with patch.object(socket.socket,'connect',side_effect=AssertionError('No network during preload')):
    with patch.object(dashboard,'initialize_dashboard',side_effect=AssertionError('No runtime initialization')):
        boot.preload_dependencies()
assert 'pandas' in sys.modules
assert 'altair' not in sys.modules
assert 'campaigniq_private_account_dashboard' not in sys.modules
first=report_module();second=report_module()
assert first is not second
first.ARTIFACT_STORAGE='private account snapshot'
assert getattr(second,'ARTIFACT_STORAGE',None)!='private account snapshot'
assert all(vars(m).get('ARTIFACT_STORAGE')!='private account snapshot' for m in sys.modules.values() if m is not None)
'''
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[1]/'src'))
    result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,cwd=tmp_path)
    assert result.returncode==0,result.stderr
    assert list(tmp_path.iterdir())==[]


def test_real_streamlit_cli_serves_health_after_preload(tmp_path):
    import socket
    import time
    import urllib.request
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    # Source verification and its rejection paths are covered above. This
    # isolated smoke test exercises the actual Streamlit CLI/server, no account.
    code='''
import sys,os
from campaigniq import account_server_boot as boot
from campaigniq import account_deployment_startup as startup
from campaigniq import account_release_config as release

startup.read_release=lambda *a,**kw:object()
release.verify_source=lambda *a,**kw:None
sys.argv=['boot','--repo',os.environ['BOOT_TEST_REPO']]
boot.main()
'''
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[1]/'src'),PORT=str(port),BOOT_TEST_REPO=str(REPO),
             CAMPAIGNIQ_ACCOUNT_RELEASE_FILE='/synthetic/verified-release.json',
             CAMPAIGNIQ_ACCOUNT_RELEASE_SHA256='a'*64)
    log=tmp_path/'server.log'
    with log.open('w+') as stream:
        process=subprocess.Popen([sys.executable,'-u','-c',code],env=env,cwd=tmp_path,stdout=stream,stderr=stream)
        try:
            deadline=time.monotonic()+20
            ready=False
            while time.monotonic()<deadline and process.poll() is None:
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/_stcore/health',timeout=0.3) as response:
                        ready=response.read()==b'ok'
                    if ready:break
                except OSError:time.sleep(0.1)
            assert ready,log.read_text()
            text=log.read_text()
            assert 'campaigniq.server_boot.v1' in text
            assert 'Traceback' not in text
        finally:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


def test_installed_module_location_cannot_change_verified_repository(setup,monkeypatch):
    # Matches a normal wheel installation instead of the staged src-path layout.
    monkeypatch.setattr(boot,'__file__','/synthetic/venv/lib/python3.13/site-packages/campaigniq/account_server_boot.py')
    boot.main()
    assert setup==['read','verify','preload','serve']


@pytest.mark.parametrize('kind',['missing','relative','nonexistent'])
def test_explicit_repository_required_before_source_check_or_server(setup,monkeypatch,tmp_path,kind):
    if kind=='missing':args=['boot']
    elif kind=='relative':args=['boot','--repo','relative-checkout']
    else:args=['boot','--repo',str(tmp_path/'missing')]
    monkeypatch.setattr(sys,'argv',args)
    with pytest.raises((ValueError,SystemExit)):boot.main()
    assert setup==[]


def test_verified_launcher_passes_checkout_root_to_installed_boot(tmp_path,monkeypatch):
    import importlib.util
    root=tmp_path/'checkout';(root/'scripts').mkdir(parents=True);(root/'.streamlit').mkdir()
    script=root/'scripts/start_accounts.py';script.write_bytes((REPO/'scripts/start_accounts.py').read_bytes())
    (root/'.streamlit/secrets.toml').write_text('[auth]\nclient_id="synthetic"\n')
    runtime=tmp_path/'runtime.json'
    runtime.write_text(json.dumps(dict(format='campaigniq.private_account_runtime',version=1,release_sha256='a'*64,
        connections={name:'synthetic' for _,name in release.ROLE_ENV},credential_packet_sha256='b'*64)))
    runtime.chmod(0o600)
    spec=importlib.util.spec_from_file_location('installed_boot_launcher',script)
    launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
    monkeypatch.setattr(sys,'argv',[str(script)])
    monkeypatch.setenv('CAMPAIGNIQ_ACCOUNT_RUNTIME_FILE',str(runtime));monkeypatch.setenv('PORT','8501')
    for key in [name for _,name in release.ROLE_ENV]+['CAMPAIGNIQ_ACCOUNT_RELEASE_FILE','CAMPAIGNIQ_ACCOUNT_RELEASE_SHA256']:
        monkeypatch.setenv(key,'previous-synthetic')
    monkeypatch.setattr(startup,'read_release',lambda *a,**kw:SimpleNamespace(publication_enabled=True,continue_existing=True))
    monkeypatch.setattr(startup,'compose_release',lambda *a,**kw:object())
    monkeypatch.setattr(startup,'verify_startup',lambda *a,**kw:None)
    execute=Mock();monkeypatch.setattr(launcher.os,'execv',execute)
    launcher.main()
    execute.assert_called_once_with(sys.executable,[sys.executable,'-m','campaigniq.account_server_boot','--repo',str(root)])


def test_source_verified_boot_with_package_installed_outside_checkout(tmp_path):
    import shutil
    from campaigniq.account_schema_migrations import digest
    from campaigniq.account_release_config import source_inventory
    from tests.test_account_release_startup import proposal
    checkout=tmp_path/'checkout';installed=tmp_path/'venv/lib/site-packages';other=tmp_path/'other';other.mkdir()
    for destination in (checkout/'src/campaigniq',installed/'campaigniq'):
        shutil.copytree(REPO/'src/campaigniq',destination,ignore=shutil.ignore_patterns('__pycache__'))
    value=proposal(tmp_path/'config-template');value['source_files']=source_inventory(checkout)
    release_file=tmp_path/'release.private.json';release_file.write_text(json.dumps(value));release_file.chmod(0o600)
    code='''
import sys,socket,runpy,psycopg,importlib.util,os
assert importlib.util.find_spec('campaigniq.account_server_boot').origin.startswith(os.environ['INSTALLED_BOOT_TEST'])
def denied(*a,**kw):raise AssertionError('No connections while loading code')
psycopg.connect=denied
psycopg.Connection.connect=denied
socket.socket.connect=denied
sys.argv=['boot','--repo',os.environ['CHECKOUT_BOOT_TEST'],'--check-only']
runpy.run_module('campaigniq.account_server_boot',run_name='__main__')
'''
    env=dict(os.environ,PYTHONPATH=str(installed),INSTALLED_BOOT_TEST=str(installed),CHECKOUT_BOOT_TEST=str(checkout),
             CAMPAIGNIQ_ACCOUNT_RELEASE_FILE=str(release_file),CAMPAIGNIQ_ACCOUNT_RELEASE_SHA256=digest(value))
    result=subprocess.run([sys.executable,'-B','-c',code],env=env,cwd=other,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    log=json.loads(result.stdout)
    assert log['event']=='campaigniq.server_boot.v1'
    assert list(other.iterdir())==[]
