import os
import pytest

@pytest.mark.skipif(os.environ.get('CAMPAIGNIQ_DEPLOYMENT_DASHBOARD_TEST')!='1',reason='Explicit disposable local clone dashboard rehearsal required')
def test_dashboard_and_comparison_parity_on_committed_clone(tmp_path):
    from campaigniq.account_committed_publication_rehearsal import run_rehearsal
    report=run_rehearsal(tmp_path/'dashboard',development_only=True,verify_dashboard=True)
    assert report['passed'] and report['disposable_database_and_roles_removed']
    assert len(report['checks'])==18 and all(report['checks'].values())
