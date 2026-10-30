"""Opt-in DDL rehearsal on pinned LOCAL full restore; always rolls back."""
import os
import pytest

@pytest.mark.skipif(os.environ.get('CAMPAIGNIQ_ACCOUNT_SCHEMA_RESTORE_TEST')!='1',reason='Explicit isolated full restore required')
def test_schema_on_local_restore(tmp_path):
    from campaigniq.account_schema_rehearsal import run_rehearsal
    report=run_rehearsal(tmp_path/'rehearsal',development_only=True)
    assert report['passed'] and len(report['checks'])==7 and all(report['checks'].values())
