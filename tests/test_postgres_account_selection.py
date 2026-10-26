"""Opt-in actual PostgreSQL account selection and snapshot rehearsal."""
import os
import pytest


def test_postgres_account_selection(tmp_path):
    dsn=os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL')
    if not dsn:pytest.skip('Explicit local admin test database not selected')
    from campaigniq.account_selection_rehearsal import run_rehearsal
    report=run_rehearsal(tmp_path/'account-selection',dsn)
    assert report['local_rehearsal_passed'] and len(report['checks'])==8
    assert all(report['checks'].values())
    assert not report['production_connection_made'] and not report['publication_authorized']
