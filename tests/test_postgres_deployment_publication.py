import os
import pytest

@pytest.mark.skipif(os.environ.get('CAMPAIGNIQ_DEPLOYMENT_PUBLICATION_RESTORE_TEST')!='1',reason='Explicit local superuser rollback rehearsal required')
def test_restricted_deployment_publication_on_local_restore(tmp_path):
    from campaigniq.account_deployment_publication_rehearsal import run_rehearsal
    report=run_rehearsal(tmp_path/'publication',development_only=True)
    assert report['passed'] and len(report['checks'])==10 and all(report['checks'].values())
