"""Opt-in actual LOCAL synthetic financial publication, rollback and continuity rehearsal."""
import os
import pytest


def test_postgres_account_publication(tmp_path):
    dsn=os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL')
    if not dsn:pytest.skip('Explicit local admin test database not selected')
    from campaigniq.account_publication_rehearsal import run_rehearsal
    report=run_rehearsal(tmp_path/'local-publication',dsn)
    assert report['local_rehearsal_passed'] and len(report['checks'])==10
    assert all(report['checks'].values())
    assert not report['production_connection_made'] and not report['production_publication_authorized']
