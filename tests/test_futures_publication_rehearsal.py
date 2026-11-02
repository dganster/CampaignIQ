import base64
import json
import os
import subprocess
import sys
import zipfile
import pytest

from campaigniq import account_runtime_roles_admin as admin


def test_futures_rehearsal_bundle_imports_isolated_with_fixed_synthetic_fixtures(tmp_path):
    archive=tmp_path/'futures.zip';admin.build_bundle(archive,job='futures')
    with zipfile.ZipFile(archive) as z:
        manifest=json.loads(z.read('bundle.json'))
        assert manifest['job']=='futures'
        from campaigniq.account_reconciliation_rehearsal import ROLES
        assert {n for n in z.namelist() if n.startswith('tests/')}=={'tests/fixtures/'+n for n in ROLES.values()}
        z.extractall(tmp_path/'isolated')
    code="import sys;sys.path[:0]=sys.argv[1:];from campaigniq.account_futures_publication_rehearsal import verify_futures;from campaigniq.futures_rehearsal_sources import sources,statement_pdf;assert len(sources())==4;assert statement_pdf().startswith(b'%PDF-')"
    result=subprocess.run([sys._base_executable,'-I','-S','-c',code,str(tmp_path/'isolated/code'),str(tmp_path/'isolated/deps')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    output=tmp_path/'reports';output.mkdir()
    line=admin.PREFIX+json.dumps(dict(name='futures-publication-report.json',data=base64.b64encode(b'{"passed":true}').decode()))
    admin.receive_report(output,line)
    assert (output/'futures-publication-report.json').stat().st_mode&0o777==0o600


@pytest.mark.parametrize('other',['publication','committed','dashboard','preferences','application'])
def test_futures_rehearsal_rejects_combined_jobs_before_any_admin_command(tmp_path,other):
    with pytest.raises(ValueError):admin.run_admin_rehearsal(tmp_path/'absent',development_only=True,futures=True,**{other:True})
    assert not (tmp_path/'absent').exists()


@pytest.mark.skipif(os.environ.get('CAMPAIGNIQ_FUTURES_PUBLICATION_TEST')!='1',reason='Explicit local postgres disposable-clone rehearsal required')
def test_actual_restricted_futures_generation_on_disposable_clone(tmp_path):
    from campaigniq.account_committed_publication_rehearsal import run_rehearsal
    report=run_rehearsal(tmp_path/'futures',development_only=True,verify_futures=True)
    assert report['passed'] and report['disposable_database_and_roles_removed']
    assert report['checks']['independent_reader_cannot_see_uncommitted_futures_and_simultaneous_publications_have_one_winner']
