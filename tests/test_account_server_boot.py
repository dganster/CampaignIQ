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


@pytest.fixture
def setup(monkeypatch):
    order=[]
    config=object()
    monkeypatch.setenv('CAMPAIGNIQ_ACCOUNT_RELEASE_FILE','/private/release.private.json')
    monkeypatch.setenv('CAMPAIGNIQ_ACCOUNT_RELEASE_SHA256','a'*64)
    monkeypatch.setenv('PORT','9876')
    monkeypatch.setattr(sys,'argv',['boot'])
    def read(path,**kw):
        assert path=='/private/release.private.json' and kw==dict(reviewed_sha256='a'*64)
        order.append('read');return config
    def verify(c,root):
        assert c is config and root==Path(boot.__file__).resolve().parents[2]
        order.append('verify')
    monkeypatch.setattr(startup,'read_release',read)
    monkeypatch.setattr(release,'verify_source',verify)
    monkeypatch.setattr(boot,'preload_dependencies',lambda:order.append('preload'))
    def run(name,**kw):
        assert order==['read','verify','preload']
        assert name=='streamlit' and kw==dict(run_name='__main__',alter_sys=True)
        assert sys.argv==['streamlit','run',str(Path(boot.__file__).resolve().parents[2]/'src/campaigniq/ui/account_deployment_entrypoint.py'),
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
    monkeypatch.setattr(sys,'argv',['boot','--check-only'])
    boot.main();assert setup==['read','verify','preload']


@pytest.mark.parametrize('cause',['source','preload','port','arguments'])
def test_boot_rejects_failure_before_serving(setup,monkeypatch,cause):
    if cause=='source':monkeypatch.setattr(release,'verify_source',Mock(side_effect=ValueError('source changed')))
    elif cause=='preload':monkeypatch.setattr(boot,'preload_dependencies',Mock(side_effect=ImportError('dependency unavailable')))
    elif cause=='port':monkeypatch.setenv('PORT','70000')
    else:monkeypatch.setattr(sys,'argv',['boot','unreviewed.py'])
    with pytest.raises((ValueError,ImportError)):boot.main()
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
import sys
from campaigniq import account_server_boot as boot
from campaigniq import account_deployment_startup as startup
from campaigniq import account_release_config as release
startup.read_release=lambda *a,**kw:object()
release.verify_source=lambda *a,**kw:None
sys.argv=['boot']
boot.main()
'''
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[1]/'src'),PORT=str(port),
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
