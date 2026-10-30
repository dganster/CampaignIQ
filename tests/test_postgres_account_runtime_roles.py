import os
import pytest

@pytest.mark.skipif(os.environ.get('CAMPAIGNIQ_ACCOUNT_ROLE_RESTORE_TEST')!='1',reason='Explicit local full-restore role rehearsal required')
def test_runtime_roles_rollback_on_fixed_local_restore(tmp_path):
    from campaigniq.account_runtime_roles_rehearsal import run_rehearsal
    report=run_rehearsal(tmp_path/'roles',development_only=True)
    assert report['passed'] and len(report['checks'])==9 and all(report['checks'].values())
