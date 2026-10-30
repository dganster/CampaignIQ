import os
import pytest

@pytest.mark.skipif(os.environ.get('CAMPAIGNIQ_DEPLOYMENT_APPLICATION_TEST')!='1',reason='Explicit disposable local clone application rehearsal required')
def test_deployment_application_lifecycle_on_restricted_disposable_clone(tmp_path):
    from campaigniq.account_committed_publication_rehearsal import run_rehearsal
    report=run_rehearsal(tmp_path/'application',development_only=True,verify_application=True)
    assert report['passed'] and report['disposable_database_and_roles_removed']
    assert len(report['checks'])==23 and all(report['checks'].values())
