"""Actual publication engine under simulated transactions; SQL contract is opt-in."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest
from campaigniq import account_signedin_publication as module
from campaigniq.account_administration import Permission
from campaigniq.account_legacy_access_rehearsal import RehearsalSession
from campaigniq.account_signin import signed_in_accounts
from campaigniq.account_signedin_inputs import SignedInMonthlyInputs
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.signedin_account_data import LocalSignedInInputGateway
from campaigniq.persistence.lot_book_store import load_lot_book_from_storage
from tests.test_account_local_publication import local, month, bound_context, context, FIXTURES


@pytest.fixture
def signed(local,monkeypatch):
    c=local
    gateway=object.__new__(LocalSignedInInputGateway)
    gateway._directory=c.bound.gateway._directory
    gateway._issuer=c.admin.issuer
    c.signed=SignedInMonthlyInputs(gateway)
    c.user=RehearsalSession(c.admin)
    @contextmanager
    def transaction(g,user,account):
        actor=signed_in_accounts(user,c.directory,trusted_issuer=c.admin.issuer)
        c.directory.authorize(actor.identity,account,write=True)
        before=dict(c.artifacts[account].rows)
        try:
            with c.bound.gateway.transaction(actor.identity,account,write=True) as session:
                yield session,actor.user_id
        except BaseException:
            c.artifacts[account].rows=before
            raise
    monkeypatch.setattr(module,'_publication_transaction',transaction)
    @contextmanager
    def normal(user,account,*,write=False):
        actor=signed_in_accounts(user,c.directory,trusted_issuer=c.admin.issuer)
        with c.bound.gateway.transaction(actor.identity,account,write=write) as session:
            yield session
    gateway.transaction=normal
    return c


def publish(c,**kw):
    return c.signed.publish(c.user,c.account,c.draft.id,c.revision,
        authorize_local_publication=True,forex_applicable=False,crypto_applicable=False,**kw)


def test_actual_engine_receipt_sources_and_published_reader(signed):
    c=signed;before=dict(c.repositories[c.account].docs)
    assert not c.signed.published(c.user,c.account,c.draft.id,c.revision)
    result=publish(c)
    assert result.local_published and not result.production_publication_authorized
    assert c.signed.published(c.user,c.account,c.draft.id,c.revision)
    receipt=json.loads(c.artifacts[c.account].read_text('2025-01-account-publication-receipt.json'))
    assert receipt['format']=='campaigniq.signedin_local_account_publication'
    assert receipt['actor_id']==c.directory.user(c.admin).id
    assert receipt['draft_revision']==4 and len(receipt['original_documents'])==4
    assert receipt['forex_applicable'] is False and receipt['crypto_applicable'] is False
    assert before==c.repositories[c.account].docs


@pytest.mark.parametrize('authorization',[False,None,1,'yes'])
def test_confirmation_required_before_connection(signed,authorization,monkeypatch):
    def forbidden(*a,**k):raise AssertionError('Unauthorized connection')
    monkeypatch.setattr(module,'_publication_transaction',forbidden)
    with pytest.raises(PermissionError):
        signed.signed.publish(signed.user,signed.account,signed.draft.id,signed.revision,
            authorize_local_publication=authorization)


@pytest.mark.parametrize('revision,forex,crypto',[(True,False,False),(0,1,False),(0,False,'false')])
def test_strict_revision_and_flags(signed,revision,forex,crypto):
    c=signed
    with pytest.raises(ValueError):c.signed.publish(c.user,c.account,c.draft.id,revision,
        authorize_local_publication=True,forex_applicable=forex,crypto_applicable=crypto)
    assert not c.artifacts[c.account].rows


def test_stale_or_foreign_draft_never_commits(signed):
    c=signed
    with pytest.raises(ValueError,match='Draft changed'):
        c.signed.publish(c.user,c.account,c.draft.id,0,authorize_local_publication=True)
    with pytest.raises(FileNotFoundError):
        c.signed.publish(c.user,c.account,'foreign',0,authorize_local_publication=True)
    assert not c.artifacts[c.account].rows


def test_mismatch_rolls_back_every_staged_artifact(signed):
    c=signed
    c.revision=c.bound.save(c.admin,c.account,c.draft.id,c.revision,'schwab_brokerage_statement',
        RestoredUpload('mismatch.txt','text/plain',(FIXTURES/'account-month-closing-mismatch.txt').read_bytes()),replace=True)
    with pytest.raises(ValueError,match='does not reconcile'):publish(c)
    assert not c.artifacts[c.account].rows


def test_failure_after_marker_rolls_back_whole_generation(signed,monkeypatch):
    c=signed;storage=c.artifacts[c.account];write=storage.write_text
    def injected(key,content):
        write(key,content)
        if key=='2025-01-finalized.json':raise RuntimeError('post marker failure')
    monkeypatch.setattr(storage,'write_text',injected)
    with pytest.raises(RuntimeError):publish(c)
    assert not storage.rows


def test_published_generation_and_originals_cannot_be_replaced(signed):
    c=signed;publish(c);before=dict(c.artifacts[c.account].rows)
    with pytest.raises(ValueError,match='already authoritative'):publish(c)
    with pytest.raises(ValueError,match='Published month documents'):
        c.signed.save(c.user,c.account,c.draft.id,c.revision,'schwab_brokerage_statement',
            RestoredUpload('replacement.txt','text/plain',b'blocked'),replace=True)
    assert c.artifacts[c.account].rows==before


def test_current_session_view_and_archive_guards(signed):
    c=signed
    with pytest.raises(PermissionError):c.signed.publish(c.admin,c.account,c.draft.id,c.revision,
        authorize_local_publication=True)
    c.directory=c.directory.set_grant(c.admin,c.directory.user(c.member).id,c.account,Permission.VIEW,
        expected_revision=c.directory.revision)
    with pytest.raises(PermissionError):c.signed.publish(RehearsalSession(c.member),c.account,c.draft.id,c.revision,
        authorize_local_publication=True)
    c.directory=c.directory.configure_account(c.admin,c.account,archived=True,expected_revision=c.directory.revision)
    with pytest.raises(PermissionError):publish(c)
    assert not c.artifacts[c.account].rows


def test_following_month_uses_committed_opening_and_gap_backfill_blocked(signed):
    from campaigniq.account_publication_rehearsal import february_sources
    c=signed;publish(c)
    feb=c.bound.create(c.admin,c.account,2025,2);revision=0
    for role,upload in february_sources().items():revision=c.bound.save(c.admin,c.account,feb.id,revision,role,upload)
    c.signed.publish(c.user,c.account,feb.id,revision,authorize_local_publication=True,
        forex_applicable=False,crypto_applicable=False)
    state=load_lot_book_from_storage(c.artifacts[c.account],'2025-02-lot-book.json')
    assert sum(l.quantity for lots in state.lot_book._lots.values() for l in lots)==115
    for year,month,message in ((2024,12,'Historical backfill blocked'),(2025,4,'exact preceding published month')):
        draft=c.bound.create(c.admin,c.account,year,month)
        before=dict(c.artifacts[c.account].rows)
        with pytest.raises(ValueError,match=message):c.signed.publish(c.user,c.account,draft.id,0,
            authorize_local_publication=True,forex_applicable=False,crypto_applicable=False)
        assert c.artifacts[c.account].rows==before


def test_synthetic_entrypoint_still_rejects_signedin_service(signed):
    from campaigniq.account_local_publication import publish_synthetic_month
    c=signed
    with pytest.raises(ValueError):publish_synthetic_month(c.signed,c.user,c.account,c.draft.id,c.revision,
        authorize_synthetic_publication=True)


def test_real_writer_context_holds_locks_scopes_account_and_expires_handles(local,monkeypatch):
    c=local;gateway=object.__new__(LocalSignedInInputGateway);events=[];sessions=[]
    c.directory=c.directory.add_account(c.admin,owner_id=c.directory.user(c.admin).id,
        name='New local account',broker='schwab',expected_revision=c.directory.revision)
    c.account=next(a.id for a in c.directory.accounts if not a.legacy)
    conn=SimpleNamespace(execute=lambda sql,args=None:events.append((sql,args)))
    @contextmanager
    def connection():
        events.append('begin')
        try:yield conn
        except BaseException:
            events.append('rollback');raise
        else:events.append('commit')
    gateway._directory=SimpleNamespace(registry_id=c.registry,_connection=connection)
    gateway._current=lambda conn,user:(c.directory,signed_in_accounts(user,c.directory,trusted_issuer=c.admin.issuer))
    gateway._ready=lambda conn:True
    class Session:
        def __init__(self,connection,access,writable,registry):
            assert writable is True
            self._active=True;self._physical_scope='';sessions.append(self)
            self.artifacts=SimpleNamespace()
        @property
        def _physical_scope(self):return self.scope
        @_physical_scope.setter
        def _physical_scope(self,value):
            self.scope=value
            if hasattr(self,'artifacts'):self.artifacts.namespace=value
        def _check(self):assert self._active
    monkeypatch.setattr(module,'AccountDataSession',Session)
    user=RehearsalSession(c.admin)
    with module._publication_transaction(gateway,user,c.account) as (session,actor):
        assert actor==c.directory.user(c.admin).id and session._active
        assert session._physical_scope.startswith('signedin-')
        assert 'commit' not in events
    assert not sessions[0]._active and events[-1]=='commit'
    assert 'FOR SHARE' in events[1][0]
    assert any('pg_advisory_xact_lock' in e[0] for e in events if isinstance(e,tuple))
    with pytest.raises(RuntimeError):
        with module._publication_transaction(gateway,user,c.account):raise RuntimeError('injected')
    assert not sessions[-1]._active and events[-1]=='rollback'
    with pytest.raises(PermissionError):
        with module._publication_transaction(gateway,c.admin,c.account):pass
    assert events[-1]=='rollback'


def test_incomplete_or_corrupt_published_generation_is_not_reported_as_ready(signed):
    c=signed;storage=c.artifacts[c.account]
    storage.rows['2025-01-lot-book.json']='orphan'
    with pytest.raises(ValueError,match='Incomplete local generation'):
        c.signed.published(c.user,c.account,c.draft.id,c.revision)
    storage.rows.clear();publish(c)
    receipt=json.loads(storage.rows['2025-01-account-publication-receipt.json'])
    receipt['draft_revision']=0
    storage.rows['2025-01-account-publication-receipt.json']=json.dumps(receipt)
    with pytest.raises(ValueError,match='source receipt differ'):
        c.signed.published(c.user,c.account,c.draft.id,c.revision)
