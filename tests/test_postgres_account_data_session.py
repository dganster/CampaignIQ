"""Actual local SQL isolation/atomicity rehearsal, opt-in only."""
import os

import pytest


def test_postgres_account_data_rehearsal(tmp_path):
    dsn = os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('Explicit local admin test database not selected')
    from campaigniq.account_data_rehearsal import run_rehearsal
    report = run_rehearsal(tmp_path / 'account-data', dsn)
    assert report['local_rehearsal_passed']
    assert len(report['checks']) == 12 and all(report['checks'].values())
    assert report['production_connection_made'] is False
