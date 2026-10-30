import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import subprocess
import sys
import zipfile
import pytest
from campaigniq import account_runtime_roles_admin as a


def record(name='account-runtime-roles-report.json',value=None):
    return a.PREFIX+json.dumps(dict(name=name,data=base64.b64encode(json.dumps(value or {'passed':True}).encode()).decode()))+'\n'


def test_reports_are_private_owned_by_launcher_and_cannot_escape(tmp_path):
    name,value=a.receive_report(tmp_path,record())
    assert value['passed'] and (tmp_path/name).stat().st_mode&0o777==0o600
    with pytest.raises(ValueError,match='receipt name'):a.receive_report(tmp_path,record('../outside.json'))
    assert not (tmp_path.parent/'outside.json').exists()
    with pytest.raises(FileExistsError):a.receive_report(tmp_path,record())


def test_bundle_contains_only_source_and_driver_and_imports_isolated(tmp_path):
    destination=tmp_path/'bundle.zip';checksum=a.build_bundle(destination)
    assert len(checksum)==64
    with zipfile.ZipFile(destination) as archive:
        manifest=json.loads(archive.read('bundle.json'))
        assert all(name.startswith(('code/campaigniq/','deps/')) for name in manifest['sha256'])
        assert not any('secrets.toml' in name or '.env' in name for name in archive.namelist())
        archive.extractall(tmp_path/'unpacked')
    command=[sys._base_executable,'-I','-c',
        "import sys;sys.path[:0]=sys.argv[1:];from campaigniq import account_runtime_roles_rehearsal;import psycopg;assert psycopg.pq.__impl__=='binary'"]
    subprocess.run(command+[str(tmp_path/'unpacked/code'),str(tmp_path/'unpacked/deps')],check=True,capture_output=True,text=True)


def test_administrator_command_is_existing_postgres_user_isolated_system_python():
    with patch.object(a.shutil,'which',return_value='/usr/bin/sudo'),patch.object(a.sys,'_base_executable','/usr/bin/python3'):
        command=a.administrator_command()
    assert command[:4]==['/usr/bin/sudo','-u','postgres','--']
    assert command[-3:]==['-I','-c',a.WORKER]
    assert '-S' not in command and 'root' not in command


def test_home_interpreter_and_missing_sudo_stop_before_launch():
    with patch.object(a.sys,'_base_executable','/home/private/python'),pytest.raises(ValueError,match='System Python'):
        a.administrator_command()
    with patch.object(a.shutil,'which',return_value=None),pytest.raises(ValueError):a.administrator_command()


def test_launcher_receives_all_reports_and_runs_no_normal_user_database_connection(tmp_path):
    output=tmp_path/'reports'
    lines=record('prewrite-recovery-receipt.json')+record('reviewed-grant-plan.json')+record()
    process=SimpleNamespace(stdout=io.StringIO(lines),wait=lambda:0)
    def bundle(path):path.write_bytes(b'synthetic');return 'a'*64
    with patch.object(a,'restore_conninfo',return_value='never connected') as guard,patch.object(a,'administrator_command',return_value=['synthetic-admin']),patch.object(a,'build_bundle',side_effect=bundle),patch.object(a.subprocess,'Popen',return_value=process) as launch:
        assert a.run_admin_rehearsal(output,development_only=True)['passed']
    guard.assert_called_once_with(development_only=True)
    assert launch.call_args.args[0]==['synthetic-admin']
    assert (output/'administrator-rehearsal-request.json').is_file()
    assert output.stat().st_mode&0o777==0o700


def test_failed_admin_cannot_be_reported_as_success(tmp_path):
    process=SimpleNamespace(stdout=io.StringIO(''),wait=lambda:1)
    def bundle(path):path.write_bytes(b'synthetic');return 'a'*64
    with patch.object(a,'restore_conninfo'),patch.object(a,'administrator_command',return_value=['synthetic']),patch.object(a,'build_bundle',side_effect=bundle),patch.object(a.subprocess,'Popen',return_value=process),pytest.raises(RuntimeError,match='did not complete'):
        a.run_admin_rehearsal(tmp_path/'reports',development_only=True)


def test_routing_override_blocks_before_admin_or_output(tmp_path,monkeypatch):
    monkeypatch.setenv('RENDER','true')
    with patch.object(a,'administrator_command',side_effect=AssertionError('Must not launch')),pytest.raises(ValueError,match='blocked'):
        a.run_admin_rehearsal(tmp_path/'reports',development_only=True)
    assert not (tmp_path/'reports').exists()


def test_worker_refuses_normal_user_before_extracting_or_importing():
    import pwd,os
    if pwd.getpwuid(os.getuid()).pw_name=='postgres':pytest.skip('Requires ordinary test identity')
    result=subprocess.run([sys._base_executable,'-I','-c',a.WORKER],input=b'not an archive',capture_output=True)
    assert result.returncode!=0 and b'no database connection made' in result.stderr


def test_worker_checksum_failure_precedes_package_execution(tmp_path):
    import hashlib
    destination=io.BytesIO()
    code=b"raise RuntimeError('PACKAGE MUST NOT EXECUTE')"
    with zipfile.ZipFile(destination,'w') as archive:
        archive.writestr('code/campaigniq/__init__.py',code)
        archive.writestr('bundle.json',json.dumps(dict(python=list(sys.version_info[:2]),sha256={'code/campaigniq/__init__.py':'0'*64})))
    # Substitute only the OS identity check for this stdlib-bootstrap unit test;
    # no administrator process or database connection is involved.
    prelude="import os,pwd,types;os.getuid=lambda:12345;pwd.getpwuid=lambda uid:types.SimpleNamespace(pw_name='postgres');"
    result=subprocess.run([sys._base_executable,'-I','-c',prelude+a.WORKER],input=destination.getvalue(),capture_output=True)
    assert result.returncode!=0 and b'Bundle checksum differs' in result.stderr
    assert b'PACKAGE MUST NOT EXECUTE' not in result.stderr


def test_publication_bundle_includes_only_named_public_synthetic_fixtures(tmp_path):
    destination=tmp_path/'publication.zip';a.build_bundle(destination,job='publication')
    from campaigniq.account_reconciliation_rehearsal import ROLES
    with zipfile.ZipFile(destination) as archive:
        manifest=json.loads(archive.read('bundle.json'))
        assert manifest['job']=='publication'
        assert {n for n in archive.namelist() if n.startswith('tests/')}=={'tests/fixtures/'+n for n in ROLES.values()}
        archive.extractall(tmp_path/'isolated')
    code="import sys;sys.path[:0]=sys.argv[1:];from campaigniq import account_deployment_publication_rehearsal;from campaigniq.account_selection_rehearsal import synthetic_sources;assert len(synthetic_sources('12345678'))==4"
    subprocess.run([sys._base_executable,'-I','-c',code,str(tmp_path/'isolated/code'),str(tmp_path/'isolated/deps')],check=True,capture_output=True,text=True)
    with pytest.raises(ValueError):a.build_bundle(tmp_path/'bad.zip',job='arbitrary')


def test_committed_bundle_and_report_transport_remain_fixed_and_private(tmp_path):
    destination=tmp_path/'committed.zip';a.build_bundle(destination,job='committed')
    with zipfile.ZipFile(destination) as archive:
        assert json.loads(archive.read('bundle.json'))['job']=='committed'
        archive.extractall(tmp_path/'isolated')
    code="import sys;sys.path[:0]=sys.argv[1:];from campaigniq import account_committed_publication_rehearsal;from campaigniq.account_selection_rehearsal import synthetic_sources;assert len(synthetic_sources('12345678'))==4"
    subprocess.run([sys._base_executable,'-I','-c',code,str(tmp_path/'isolated/code'),str(tmp_path/'isolated/deps')],check=True,capture_output=True,text=True)
    output=tmp_path/'reports';output.mkdir()
    a.receive_report(output,record('committed-publication-report.json'))
    with pytest.raises(ValueError):a.run_admin_rehearsal(tmp_path/'never',development_only=True,publication=True,committed=True)


def test_dashboard_job_is_fixed_and_report_is_private(tmp_path):
    destination=tmp_path/'dashboard.zip';a.build_bundle(destination,job='dashboard')
    with zipfile.ZipFile(destination) as archive:
        assert json.loads(archive.read('bundle.json'))['job']=='dashboard'
        archive.extractall(tmp_path/'isolated')
    code="import sys;sys.path[:0]=sys.argv[1:];from campaigniq import account_deployment_dashboard_rehearsal,account_deployment_dashboard,account_deployment_comparison"
    subprocess.run([sys._base_executable,'-I','-c',code,str(tmp_path/'isolated/code'),str(tmp_path/'isolated/deps')],check=True,capture_output=True,text=True)
    output=tmp_path/'reports';output.mkdir()
    a.receive_report(output,record('deployment-dashboard-report.json'))
    assert (output/'deployment-dashboard-report.json').stat().st_mode&0o777==0o600
    with pytest.raises(ValueError):a.run_admin_rehearsal(tmp_path/'never',development_only=True,committed=True,dashboard=True)


def test_preferences_job_has_fixed_reports_and_rejects_combined_modes(tmp_path):
    destination=tmp_path/'preferences.zip';a.build_bundle(destination,job='preferences')
    with zipfile.ZipFile(destination) as archive:
        assert json.loads(archive.read('bundle.json'))['job']=='preferences'
        archive.extractall(tmp_path/'isolated')
    code="import sys;sys.path[:0]=sys.argv[1:];from campaigniq import account_deployment_preferences,account_preference_roles,account_deployment_preferences_rehearsal"
    result=subprocess.run([sys._base_executable,'-I','-S','-c',code,str(tmp_path/'isolated/code'),str(tmp_path/'isolated/deps')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    output=tmp_path/'reports';output.mkdir()
    for name in ('deployment-preferences-report.json','reviewed-preference-grant-plan.json'):
        a.receive_report(output,record(name))
        assert (output/name).stat().st_mode&0o777==0o600
    with pytest.raises(ValueError):a.run_admin_rehearsal(tmp_path/'never',development_only=True,dashboard=True,preferences=True)


def test_application_bundle_imports_without_site_packages_and_has_fixed_private_reports(tmp_path):
    destination=tmp_path/'application.zip';a.build_bundle(destination,job='application')
    with zipfile.ZipFile(destination) as archive:
        assert json.loads(archive.read('bundle.json'))['job']=='application'
        archive.extractall(tmp_path/'isolated')
    code="import sys;sys.path[:0]=sys.argv[1:];from campaigniq import account_deployment_application,account_deployment_application_rehearsal,account_deployment_startup,account_release_config,account_release_packet"
    result=subprocess.run([sys._base_executable,'-I','-S','-c',code,str(tmp_path/'isolated/code'),str(tmp_path/'isolated/deps')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    output=tmp_path/'reports';output.mkdir()
    a.receive_report(output,record('deployment-application-report.json'))
    assert (output/'deployment-application-report.json').stat().st_mode&0o777==0o600
    with pytest.raises(ValueError):a.run_admin_rehearsal(tmp_path/'never',development_only=True,preferences=True,application=True)
