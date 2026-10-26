"""Opt-in real local broker binding and rejection rehearsal."""
import os
import pytest


def test_postgres_broker_binding(tmp_path):
    dsn=os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL')
    if not dsn:pytest.skip('Explicit local admin test database not selected')
    from campaigniq.broker_binding_rehearsal import run_rehearsal
    report=run_rehearsal(tmp_path/'broker-binding',dsn)
    assert report['local_rehearsal_passed'] and len(report['checks'])==8
    assert all(report['checks'].values())
    assert not report['publication_authorized'] and not report['production_connection_made']
