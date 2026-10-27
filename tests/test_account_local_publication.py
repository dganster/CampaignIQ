"""Local publication uses the existing engine, append guards and exact source receipts."""
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
import pytest

from campaigniq.account_administration import Permission
from campaigniq.account_local_publication import publish_synthetic_month
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.authoritative_lot_state import load_preceding_authoritative_state_from_storage
from campaigniq.persistence.lot_book_store import load_lot_book_from_storage
from tests.test_account_reconciliation_preview import month,bound_context,context,FIXTURES


class MemoryStorage:
    def __init__(self,namespace):self.namespace=namespace;self.rows={}
    def exists(self,key):return key in self.rows
    def read_text(self,key):return self.rows[key]
    def write_text(self,key,text):self.rows[key]=text
    def delete(self,key):self.rows.pop(key,None)
    def list_keys(self,*,prefix='',suffix=''):
        return tuple(sorted(k for k in self.rows if k.startswith(prefix) and k.endswith(suffix)))


@pytest.fixture
def local(month):
    c=month;c.artifacts[c.account]=MemoryStorage(c.account)
    c.bound.gateway._directory.registry_id=c.registry
    return c


def publish(c,**kwargs):
    return publish_synthetic_month(c.bound,c.admin,c.account,c.draft.id,c.revision,
        authorize_synthetic_publication=True,forex_applicable=False,crypto_applicable=False,**kwargs)


def test_real_engine_publishes_complete_generation_and_keeps_original_sources(local):
    c=local;docs=dict(c.repositories[c.account].docs)
    result=publish(c);storage=c.artifacts[c.account]
    assert result.local_published and not result.production_publication_authorized
    assert len(result.artifact_keys)==10
    assert storage.exists('2025-01-finalized.json')
    assert storage.exists('.campaigniq-monthly-publication-v1.json')
    receipt=storage.read_text('2025-01-account-publication-receipt.json')
    assert sha256(receipt.encode()).hexdigest()==result.receipt_sha256
    assert json.loads(receipt)['draft_revision']==4
    assert json.loads(receipt)['original_documents']['thinkorswim_trade_history']['sha256']==sha256((FIXTURES/'account-month-history.csv').read_bytes()).hexdigest()
    preceding=load_preceding_authoritative_state_from_storage(storage,period_start=date(2025,2,1))
    assert preceding is not None
    assert sum(lot.quantity for lots in preceding[2]._lots.values() for lot in lots)==110
    assert c.repositories[c.account].docs==docs and c.repositories[c.account].get(c.draft.id).revision==4


@pytest.mark.parametrize('authorize',[False,None,1,'yes'])
def test_explicit_synthetic_publication_authorization_is_required(local,authorize):
    c=local;calls=len(c.calls)
    with pytest.raises(PermissionError):publish_synthetic_month(c.bound,c.admin,c.account,c.draft.id,c.revision,
        authorize_synthetic_publication=authorize,forex_applicable=False,crypto_applicable=False)
    assert len(c.calls)==calls and not c.artifacts[c.account].rows


def test_replacement_of_published_month_is_blocked_without_changing_artifacts(local):
    c=local;publish(c);before=dict(c.artifacts[c.account].rows)
    with pytest.raises(ValueError,match='already authoritative'):publish(c)
    assert c.artifacts[c.account].rows==before


def test_stale_draft_and_mismatch_are_rejected_before_publication(local):
    c=local
    with pytest.raises(ValueError,match='Draft changed'):
        publish_synthetic_month(c.bound,c.admin,c.account,c.draft.id,0,authorize_synthetic_publication=True,
            forex_applicable=False,crypto_applicable=False)
    c.revision=c.bound.save(c.admin,c.account,c.draft.id,c.revision,'schwab_brokerage_statement',
        RestoredUpload('mismatch.txt','text/plain',(FIXTURES/'account-month-closing-mismatch.txt').read_bytes()),replace=True)
    with pytest.raises(ValueError,match='does not reconcile'):publish(c)
    assert not c.artifacts[c.account].rows


def test_missing_inputs_and_wrong_identity_cannot_publish(local):
    from campaigniq.broker_account_binding import bind
    c=local
    c.directory=bind(c.directory,c.admin,c.account,'securities','87654321',expected_revision=c.directory.revision,confirm_replace=True)
    with pytest.raises(ValueError,match='identity is unverified'):publish(c)
    assert not c.artifacts[c.account].rows
    c.directory=bind(c.directory,c.admin,c.account,'securities','12345678',expected_revision=c.directory.revision,confirm_replace=True)
    c.repositories[c.account].docs.pop((c.draft.id,'schwab_realized_gain_loss'))
    with pytest.raises(ValueError,match='inputs are incomplete'):publish(c)
    assert not c.artifacts[c.account].rows


def test_view_grant_and_archived_account_cannot_publish(local):
    c=local
    c.directory=c.directory.set_grant(c.admin,c.directory.user(c.member).id,c.account,Permission.VIEW,expected_revision=c.directory.revision)
    with pytest.raises(PermissionError):publish_synthetic_month(c.bound,c.member,c.account,c.draft.id,c.revision,
        authorize_synthetic_publication=True,forex_applicable=False,crypto_applicable=False)
    c.directory=c.directory.configure_account(c.admin,c.account,archived=True,expected_revision=c.directory.revision)
    with pytest.raises(PermissionError):publish(c)
    assert not c.artifacts[c.account].rows


def test_orphaned_artifacts_are_preserved_without_adopting_or_overwriting(local):
    c=local;c.artifacts[c.account].rows['unexpected.json']='original'
    with pytest.raises(ValueError,match='unreviewed artifacts'):publish(c)
    assert c.artifacts[c.account].rows=={'unexpected.json':'original'}


def test_backfill_guard_blocks_earlier_month_and_preserves_later_state(local):
    from campaigniq.persistence.lot_book_store import serialize_lot_book
    from campaigniq.domain.lot_book import LotBook
    c=local
    c.artifacts[c.account].rows['2025-03-lot-book.json']=serialize_lot_book(period_end=date(2025,3,31),lot_book=LotBook())
    before=dict(c.artifacts[c.account].rows)
    with pytest.raises(ValueError,match='Historical backfill blocked'):publish(c)
    assert c.artifacts[c.account].rows==before


def test_existing_target_month_artifacts_cannot_be_replaced(local):
    from campaigniq.persistence.lot_book_store import serialize_lot_book
    from campaigniq.domain.lot_book import LotBook
    c=local
    c.artifacts[c.account].rows['2024-12-lot-book.json']=serialize_lot_book(period_end=date(2024,12,31),lot_book=LotBook())
    c.artifacts[c.account].rows['2025-01-realized-attributions.json']='preserved'
    before=dict(c.artifacts[c.account].rows)
    with pytest.raises(ValueError,match='Existing artifacts'):publish(c)
    assert c.artifacts[c.account].rows==before


def test_february_uses_january_opening_and_keeps_january_generation_unchanged(local):
    from campaigniq.account_publication_rehearsal import february_sources
    c=local;publish(c);before=dict(c.artifacts[c.account].rows)
    feb=c.bound.create(c.admin,c.account,2025,2);revision=0
    for role,upload in february_sources().items():revision=c.bound.save(c.admin,c.account,feb.id,revision,role,upload)
    result=publish_synthetic_month(c.bound,c.admin,c.account,feb.id,revision,
        authorize_synthetic_publication=True,forex_applicable=False,crypto_applicable=False)
    assert result.local_published
    state=load_lot_book_from_storage(c.artifacts[c.account],'2025-02-lot-book.json')
    assert sum(lot.quantity for lots in state.lot_book._lots.values() for lot in lots)==115
    assert all(c.artifacts[c.account].rows[key]==value for key,value in before.items())


def test_gap_after_published_month_is_blocked(local):
    c=local;publish(c);before=dict(c.artifacts[c.account].rows)
    march=c.bound.create(c.admin,c.account,2025,3)
    with pytest.raises(ValueError,match='exact preceding published month'):
        publish_synthetic_month(c.bound,c.admin,c.account,march.id,0,
            authorize_synthetic_publication=True,forex_applicable=False,crypto_applicable=False)
    assert c.artifacts[c.account].rows==before
