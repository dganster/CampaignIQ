"""Opt-in real local SQL preflight integration rehearsal."""
import os
import pytest


def test_postgres_account_input_preview(tmp_path):
    dsn=os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('Explicit local admin test database not selected')
    from campaigniq.account_import_preview_rehearsal import run_rehearsal
    result=run_rehearsal(tmp_path/'input-preview',dsn)
    assert result['local_rehearsal_passed']
    assert len(result['checks'])==7 and all(result['checks'].values())
    assert not result['production_connection_made'] and not result['publication_authorized']
