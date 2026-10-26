"""Opt-in real local account reconciliation and write-rejection rehearsal."""
import os
import pytest


def test_postgres_account_reconciliation(tmp_path):
    dsn=os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL')
    if not dsn:pytest.skip('Explicit local admin test database not selected')
    from campaigniq.account_reconciliation_rehearsal import run_rehearsal
    report=run_rehearsal(tmp_path/'reconciliation',dsn)
    assert report['local_rehearsal_passed'] and len(report['checks'])==7
    assert all(report['checks'].values())
    assert not report['publication_authorized'] and not report['production_connection_made']
