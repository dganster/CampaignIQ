import os
import pytest

@pytest.mark.skipif(os.environ.get('CAMPAIGNIQ_COMMITTED_PUBLICATION_TEST')!='1',reason='Explicit local superuser disposable-clone rehearsal required')
def test_committed_publication_on_disposable_local_clone(tmp_path):
    from campaigniq.account_committed_publication_rehearsal import run_rehearsal
    report=run_rehearsal(tmp_path/'committed',development_only=True)
    assert report['passed'] and report['disposable_database_and_roles_removed']
    assert len(report['checks'])==12 and all(report['checks'].values())
