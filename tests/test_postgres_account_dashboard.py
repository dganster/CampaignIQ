import os
import pytest
from campaigniq.account_dashboard_rehearsal import run_rehearsal


def test_local_account_dashboard_snapshot_contract(tmp_path):
    dsn=os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL')
    if not dsn:pytest.skip('Explicit local database opt-in required')
    result=run_rehearsal(tmp_path/'dashboard',dsn)
    assert result['local_rehearsal_passed']
    assert len(result['checks'])==6 and all(result['checks'].values())
