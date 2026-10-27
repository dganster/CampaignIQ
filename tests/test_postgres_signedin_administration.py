import os
import pytest
from campaigniq.account_signedin_admin_rehearsal import run_rehearsal


def test_local_signedin_administration_contract(tmp_path):
    dsn = os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('Explicit local database opt-in required')
    report = run_rehearsal(tmp_path / 'signedin-admin', dsn)
    assert report['local_rehearsal_passed']
    assert len(report['checks']) == 8 and all(report['checks'].values())
