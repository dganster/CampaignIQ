"""No database needed: tests the bridge contract using a fake scoped repository."""
from dataclasses import dataclass
from uuid import uuid4

import pytest

from campaigniq.import_contract import MonthlyInputRole
from campaigniq.import_draft_bridge import (
    assert_revision_unchanged, restore_uploads,
)


@dataclass
class Draft:
    revision: int


class Repo:
    def __init__(self):
        self.revision = 1
        self.docs = {"schwab_opening_position_snapshot": (
            "december.pdf", "application/pdf", b"%PDF-1.4\noriginal")}
        self.denied = False

    def get(self, draft_id):
        if self.denied:
            raise FileNotFoundError("draft not found")
        return Draft(self.revision)

    def document(self, draft_id, role):
        if self.denied:
            raise FileNotFoundError("draft not found")
        if role not in self.docs:
            raise FileNotFoundError("document not found")
        return self.docs[role]


def test_restores_original_pdf_and_allows_missing_roles():
    repo = Repo()
    restored = restore_uploads(repo, uuid4())
    upload = restored[MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT]
    assert upload.name == "december.pdf"
    assert upload.getvalue() == b"%PDF-1.4\noriginal"
    assert len(restored) == 1


def test_revalidation_required_after_replacement():
    repo = Repo()
    draft_id = uuid4()
    assert_revision_unchanged(repo, draft_id, 1)
    repo.revision = 2
    with pytest.raises(ValueError, match="validate again"):
        assert_revision_unchanged(repo, draft_id, 1)


def test_workspace_denial_is_not_treated_as_empty_draft():
    repo = Repo()
    repo.denied = True
    with pytest.raises(FileNotFoundError):
        restore_uploads(repo, uuid4())
