import os
import pytest

@pytest.mark.skipif(os.environ.get('CAMPAIGNIQ_ACCOUNT_DATA_BACKEND_RESTORE_TEST')!='1',reason='Explicit committed local restore account storage rehearsal required')
def test_committed_data_backend_on_local_restore(tmp_path):
    from campaigniq.account_data_backend_rehearsal import run_rehearsal
    report=run_rehearsal(tmp_path/'rehearsal',development_only=True)
    assert report['passed'] and report['new_schema_cleanup_verified']
    assert len(report['checks'])==12 and all(report['checks'].values())
    assert report['retained_published_periods_verified']>0
