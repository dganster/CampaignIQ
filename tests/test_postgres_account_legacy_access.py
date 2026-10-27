import os
import pytest
from campaigniq.account_legacy_access_rehearsal import run_rehearsal


def test_local_legacy_account_access_contract(tmp_path):
    dsn = os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('Explicit local account database opt-in required')
    report = run_rehearsal(tmp_path / 'legacy-access', dsn)
    assert report['local_rehearsal_passed']
    assert len(report['checks']) == 8 and all(report['checks'].values())
