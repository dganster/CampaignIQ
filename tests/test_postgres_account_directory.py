"""Explicit opt-in actual PostgreSQL transaction rehearsal, never a remote DB."""
import os

import pytest


def test_local_admin_directory_postgres_rehearsal(tmp_path):
    dsn = os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('Explicit local admin test database not selected')
    from campaigniq.account_directory_rehearsal import run_rehearsal
    report = run_rehearsal(tmp_path / 'admin-directory', dsn)
    assert report['local_rehearsal_passed'] is True
    assert len(report['checks']) == 14
    assert all(report['checks'].values())
    assert report['production_connection_made'] is False
