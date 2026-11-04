import base64
import json
import os
import subprocess
import sys
import zipfile
import pytest

from campaigniq import account_runtime_roles_admin as admin


def test_crypto_source_bundle_has_only_fixed_synthetic_inputs_and_isolated_dependencies(tmp_path):
    destination=tmp_path/'crypto.zip';admin.build_bundle(destination,job='crypto_sources')
    with zipfile.ZipFile(destination) as archive:
        assert json.loads(archive.read('bundle.json'))['job']=='crypto_sources'
        from campaigniq.account_reconciliation_rehearsal import ROLES
        assert {n for n in archive.namelist() if n.startswith('tests/')}=={'tests/fixtures/'+n for n in ROLES.values()}
        archive.extractall(tmp_path/'isolated')
    code="import sys;sys.path[:0]=sys.argv[1:];from campaigniq.account_crypto_sources_rehearsal import verify_crypto_sources;from campaigniq.crypto_account_rehearsal_sources import sources;u,p,c=sources();assert len(u)==4 and p.content.startswith(b'%PDF-') and c.content.startswith(b'%PDF-')"
    result=subprocess.run([sys._base_executable,'-I','-S','-c',code,str(tmp_path/'isolated/code'),str(tmp_path/'isolated/deps')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    output=tmp_path/'reports';output.mkdir()
    admin.receive_report(output,admin.PREFIX+json.dumps(dict(name='crypto-source-receipts-report.json',data=base64.b64encode(b'{"passed":true}').decode())))
    assert (output/'crypto-source-receipts-report.json').stat().st_mode&0o777==0o600


@pytest.mark.parametrize('other',['publication','committed','dashboard','preferences','application','futures'])
def test_crypto_receipt_mode_cannot_be_combined_with_another_admin_job(tmp_path,other):
    with pytest.raises(ValueError):admin.run_admin_rehearsal(tmp_path/'absent',development_only=True,crypto_sources=True,**{other:True})
    assert not (tmp_path/'absent').exists()


def test_public_synthetic_sources_use_actual_crypto_pdf_reader_and_account_checks(tmp_path):
    from campaigniq.crypto_account_rehearsal_sources import sources
    from campaigniq.account_crypto_statement_sources import evaluate
    from campaigniq.broker_account_binding import BrokerBinding
    from types import SimpleNamespace
    from decimal import Decimal
    uploads,p,c=sources()
    service=SimpleNamespace(_known=lambda session:{'securities':BrokerBinding.create('securities','97531864')})
    session=SimpleNamespace(drafts=SimpleNamespace(document=lambda draft,role:('history.csv','text/csv',uploads[role].content)))
    context,body=evaluate(service,session,SimpleNamespace(id='synthetic',year=2026,month=10),p.content,c.content)
    assert body['account_suffix']=='0099' and Decimal(body['totals']['full_ending_cost_basis_usd'])==500
    assert 'TEST000099' not in json.dumps(context)


@pytest.mark.skipif(os.environ.get('CAMPAIGNIQ_CRYPTO_SOURCES_TEST')!='1',reason='Explicit local postgres disposable-clone crypto source rehearsal required')
def test_actual_restricted_crypto_source_transactions_on_disposable_database(tmp_path):
    from campaigniq.account_committed_publication_rehearsal import run_rehearsal
    report=run_rehearsal(tmp_path/'crypto',development_only=True,verify_crypto=True)
    assert report['passed'] and report['disposable_database_and_roles_removed']
    assert report['checks']['caught_forbidden_sql_during_crypto_source_save_cannot_commit_partial_documents']
