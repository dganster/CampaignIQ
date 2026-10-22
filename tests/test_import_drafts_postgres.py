"""Integration tests: set CAMPAIGNIQ_TEST_DATABASE_URL to a disposable PostgreSQL DB."""
import os
from uuid import uuid4

import pytest

from campaigniq.persistence.import_drafts import DraftConflict, ImportDraftRepository

@pytest.mark.skipif(not os.environ.get('CAMPAIGNIQ_TEST_DATABASE_URL'), reason='test DB not configured')
def test_draft_resume_isolation_replacement_and_binary_roundtrip():
    dsn = os.environ['CAMPAIGNIQ_TEST_DATABASE_URL']
    workspace = 'draft-test-' + uuid4().hex
    repo = ImportDraftRepository(dsn, workspace)
    repo.initialize()
    draft = repo.create_or_resume(2025, 1)
    assert repo.create_or_resume(2025, 1).id == draft.id
    assert repo.upload(draft.id, 'opening', 'dec.pdf', 'application/pdf', b'%PDF-1.4\nfirst') == 1
    assert repo.upload(draft.id, 'opening', 'dec.pdf', 'application/pdf', b'%PDF-1.4\nfirst') == 1
    with pytest.raises(DraftConflict):
        repo.upload(draft.id, 'opening', 'dec2.pdf', 'application/pdf', b'%PDF-1.4\nsecond')
    assert repo.upload(draft.id, 'opening', 'dec2.pdf', 'application/pdf', b'%PDF-1.4\nsecond', replace=True) == 2
    assert repo.document(draft.id, 'opening') == ('dec2.pdf', 'application/pdf', b'%PDF-1.4\nsecond')
    other = ImportDraftRepository(dsn, 'other-' + uuid4().hex)
    with pytest.raises(FileNotFoundError):
        other.document(draft.id, 'opening')
    with pytest.raises(FileNotFoundError):
        other.get(draft.id)
    repo.abandon(draft.id)
    with pytest.raises(FileNotFoundError):
        repo.get(draft.id)
