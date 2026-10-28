"""Real engine fixtures plus simulated transaction orchestration; SQL is opt-in."""
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest

from campaigniq import account_signedin_reconciliation as module
from campaigniq.account_legacy_access_rehearsal import RehearsalSession
from campaigniq.account_signin import signed_in_accounts
from campaigniq.account_signedin_inputs import SignedInMonthlyInputs
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.signedin_account_data import LocalSignedInInputGateway
from tests.test_account_reconciliation_preview import month, bound_context, context, FIXTURES


@pytest.fixture
def signedin(month, monkeypatch):
    c=month
    gateway=object.__new__(LocalSignedInInputGateway)
    active=[False]; c.snapshots=[]; c.read_sessions=[]
    user=RehearsalSession(c.admin)
    def current(conn, session_user):
        actor=signed_in_accounts(session_user,c.directory,trusted_issuer=c.admin.issuer)
        return c.directory,actor
    @contextmanager
    def transaction(session_user, account_id):
        _,actor=current(None,session_user)
        with c.bound.gateway.transaction(actor.identity,account_id) as guard:
            active[0]=True
            try:yield guard
            finally:active[0]=False
    @contextmanager
    def connection(*,readonly=False):
        assert readonly is True and active[0]
        c.snapshots.append(readonly)
        yield SimpleNamespace(execute=lambda *a,**k:None)
        assert active[0]
    gateway.transaction=transaction
    gateway._current=current
    gateway._ready=lambda conn:True
    gateway._directory=SimpleNamespace(registry_id=c.registry,_connection=connection,
        _load=lambda conn:c.directory)
    class Session:
        def __init__(self,conn,access,writable,registry):
            assert writable is False and active[0]
            self._connection=conn;self.account=access;self._active=True
            self.drafts=c.repositories[access.account_id]
            self.artifacts=c.artifacts[access.account_id]
            c.read_sessions.append(self)
        def _check(self):assert self._active
    monkeypatch.setattr(module,'AccountDataSession',Session)
    c.signedin=SignedInMonthlyInputs(gateway);c.session_user=user;c.lock_active=active
    return c


def replay(c, **kwargs):
    return c.signedin.reconcile(c.session_user,c.account,c.draft.id,c.revision,
        forex_applicable=False,crypto_applicable=False,**kwargs)


def test_real_pipeline_reconciles_110_shares_and_expires_snapshot(signedin):
    c=signedin
    before=dict(c.repositories[c.account].docs)
    result=replay(c)
    assert result.inventory_reconciled and not result.publication_authorized
    assert Decimal(result.closing_quantities[0][1])==110
    assert c.snapshots==[True] and not c.lock_active[0]
    assert all(not s._active for s in c.read_sessions)
    assert before==c.repositories[c.account].docs


def test_actual_statement_mismatch_is_reported_without_override(signedin):
    c=signedin
    c.revision=c.bound.save(c.admin,c.account,c.draft.id,c.revision,'schwab_brokerage_statement',
        RestoredUpload('mismatch.txt','text/plain',(FIXTURES/'account-month-closing-mismatch.txt').read_bytes()),replace=True)
    result=replay(c)
    assert not result.inventory_reconciled and Decimal(result.mismatches[0].difference)==-1
    assert not result.publication_authorized


def test_stale_revision_rejected_before_readonly_connection(signedin):
    c=signedin
    with pytest.raises(ValueError,match='Draft changed'):
        c.signedin.reconcile(c.session_user,c.account,c.draft.id,0)
    assert not c.snapshots and not c.lock_active[0]


@pytest.mark.parametrize('revision,forex,crypto',[(True,False,False),(0,1,False),(0,False,'false')])
def test_strict_flags_rejected_before_transaction(signedin,revision,forex,crypto):
    c=signedin
    with pytest.raises(ValueError):
        c.signedin.reconcile(c.session_user,c.account,c.draft.id,revision,
            forex_applicable=forex,crypto_applicable=crypto)
    assert not c.snapshots


def test_injected_write_cannot_cross_artifact_boundary(signedin,monkeypatch):
    c=signedin
    def injected(*args,**kwargs):args[1].artifacts.write_text('blocked.json','{}')
    monkeypatch.setattr(module,'_reconcile_session',injected)
    with pytest.raises(PermissionError):replay(c)
    assert not c.lock_active[0] and all(not s._active for s in c.read_sessions)


def test_existing_synthetic_entrypoint_still_rejects_signedin_service(signedin):
    from campaigniq.account_reconciliation_preview import reconcile_saved
    c=signedin
    with pytest.raises(ValueError):reconcile_saved(c.signedin,c.session_user,c.account,c.draft.id,c.revision)


def test_server_identity_required_and_permission_rechecked(signedin):
    c=signedin
    with pytest.raises(PermissionError):c.signedin.reconcile(c.admin,c.account,c.draft.id,c.revision)
    with pytest.raises(PermissionError):c.signedin.reconcile(RehearsalSession(c.member),c.account,c.draft.id,c.revision)


def test_existing_pipeline_stays_preview_only_and_cleans_staging(signedin,monkeypatch):
    import campaigniq.monthly_import_execution as engine
    c=signedin;actual=engine.execute_monthly_import;roots=[]
    def checked(*args,**kwargs):
        assert kwargs['preview_only'] is True
        assert 'reconciliation_decision' not in kwargs and 'historical_source_root' not in kwargs
        roots.append(kwargs['authoritative_state_root'].parent)
        return actual(*args,**kwargs)
    monkeypatch.setattr(engine,'execute_monthly_import',checked)
    assert replay(c).inventory_reconciled
    assert roots and not roots[0].exists()
