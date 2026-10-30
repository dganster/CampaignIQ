import os
import pytest

@pytest.mark.skipif(os.environ.get('CAMPAIGNIQ_ACCOUNT_BACKEND_RESTORE_TEST')!='1',reason='Explicit local full restore backend rehearsal required')
def test_backend_on_local_full_restore(tmp_path):
    from campaigniq.account_backend_rehearsal import run_rehearsal
    report=run_rehearsal(tmp_path/'rehearsal',development_only=True)
    assert report['passed'] and len(report['checks'])==10 and all(report['checks'].values())
