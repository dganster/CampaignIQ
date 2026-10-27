from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from psycopg.pq import TransactionStatus

from campaigniq.account_administration import Identity, LegacyOwner, Permission, propose_legacy_directory
from campaigniq.account_legacy_access_rehearsal import RehearsalSession
from campaigniq.account_signedin_inputs import SignedInMonthlyInputs
from campaigniq.broker_account_binding import bind
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.account_data_session import AccountTransactionFailed, ClosedAccountSession, LocalAccountDataGateway
from campaigniq.persistence.account_directory_codec import decode
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
from campaigniq.persistence.signedin_account_data import DATA_SCHEMA, LocalSignedInInputGateway

ISSUER = 'https://accounts.example.test'
A, B = Identity(ISSUER, 'a'), Identity(ISSUER, 'b')


class Connection:
    def __init__(self, state):
        self.state = state
        self.info = SimpleNamespace(transaction_status=TransactionStatus.INTRANS)
        self.queries = []
        self.rowcount = 1
        self.relations = [(DATA_SCHEMA, n) for n in ('artifact_storage_dev', 'import_draft_documents', 'import_drafts')]
        self.last = ''
    def execute(self, sql, params=None):
        self.last = sql
        self.queries.append((sql, params))
        if sql.lstrip().startswith('UPDATE campaigniq_admin_dev.directories'):
            self.state[0] = decode(params[1].obj)
        return self
    def fetchall(self): return self.relations if 'FROM pg_class' in self.last else []
    def fetchone(self): return None


@pytest.fixture
def harness():
    initial = propose_legacy_directory((LegacyOwner(A, 'workspace-a', 'A', 'Legacy A', 'schwab'),
        LegacyOwner(B, 'workspace-b', 'B', 'Legacy B', 'schwab')), A)
    directory = initial
    for owner, name in ((initial.users[0].id,'New A'), (initial.users[0].id,'New B'), (initial.users[1].id,'New C')):
        directory = directory.add_account(A, owner, name, 'schwab', expected_revision=directory.revision)
    state = [directory]
    conn = Connection(state)
    store = object.__new__(LocalPostgresAccountDirectory)
    store.registry_id = str(uuid4())
    store._load = lambda connection, **kwargs: state[0]
    store._append_event = Mock()
    @contextmanager
    def connection(**kwargs): yield conn
    store._connection = connection
    gateway = LocalSignedInInputGateway(store, trusted_issuer=ISSUER, authorization={'a':'workspace-a','b':'workspace-b'})
    a,b,c = (account.id for account in directory.accounts if not account.legacy)
    return SimpleNamespace(store=store, gateway=gateway, service=SignedInMonthlyInputs(gateway),
        state=state, conn=conn, a=a, b=b, c=c, legacy=initial.accounts[0].id,
        sa=RehearsalSession(A), sb=RehearsalSession(B), initial=directory)


def test_new_accounts_use_new_schema_and_registry_account_specific_scope(harness):
    h = harness
    scopes = []
    for account in (h.a,h.b):
        with h.gateway.transaction(h.sa, account, write=True) as session:
            scopes.append(session.drafts.workspace)
            assert session._writable
    assert scopes[0] != scopes[1] and all(s.startswith('signedin-') for s in scopes)
    assert all('workspace-a' != s and not s.startswith('preview-') for s in scopes)
    assert any('SET LOCAL search_path=campaigniq_signedin_accounts_dev' in sql for sql,_ in h.conn.queries)


def test_missing_dedicated_tables_never_fall_back_to_legacy(harness):
    h = harness
    h.conn.relations = [('public', n) for _,n in h.conn.relations]
    with pytest.raises(ValueError):
        with h.gateway.transaction(h.sa, h.a): pass
    assert not any('SET LOCAL search_path=' in sql for sql,_ in h.conn.queries)


def test_legacy_account_upload_and_binding_rejected(harness):
    h = harness
    with pytest.raises(ValueError):
        with h.gateway.transaction(h.sa,h.legacy,write=True): pass
    with pytest.raises(ValueError): h.gateway.bind(h.sa,h.legacy,'securities','12345678',expected_revision=3)
    assert not any(sql.lstrip().startswith(('CREATE','UPDATE','INSERT')) for sql,_ in h.conn.queries)


def test_unsupported_broker_cannot_use_schwab_binding_or_uploads(harness):
    h = harness
    h.state[0] = h.state[0].add_account(A,h.initial.users[0].id,'Other broker','Fidelity',expected_revision=3)
    other = h.state[0].accounts[-1].id
    with pytest.raises(ValueError): h.gateway.bind(h.sa,other,'securities','12345678',expected_revision=4)
    with pytest.raises(ValueError):
        with h.gateway.transaction(h.sa,other,write=True): pass


@pytest.mark.parametrize('user', [A, {'iss':ISSUER,'sub':'a','is_logged_in':True},
    RehearsalSession(A, logged_in=False), RehearsalSession(Identity('https://other.test','a'))])
def test_real_server_session_is_required(harness,user):
    with pytest.raises(PermissionError): harness.gateway.overview(user)
    with pytest.raises(PermissionError):
        with harness.gateway.transaction(user,harness.a,write=True): pass


def test_admin_does_not_gain_other_owners_financial_access(harness):
    h = harness
    assert [a.id for a in h.gateway.overview(h.sa).accounts] == [h.a,h.b]
    with pytest.raises(PermissionError):
        with h.gateway.transaction(h.sa,h.c): pass


def test_member_cannot_initialize_or_bind(harness):
    h = harness
    with pytest.raises(PermissionError): h.gateway.initialize(h.sb)
    with pytest.raises(PermissionError): h.gateway.bind(h.sb,h.c,'securities','12345678',expected_revision=3)
    assert not any(sql.lstrip().startswith(('CREATE','UPDATE','INSERT')) for sql,_ in h.conn.queries)


def test_binding_audits_authenticated_actor_and_retains_only_suffix(harness):
    h = harness
    assert h.gateway.bind(h.sa,h.a,'securities','12345678',expected_revision=3) == 4
    assert h.state[0].events[-1].actor_id == h.initial.users[0].id
    assert '12345678' not in repr(h.state[0].events[-1])
    assert h.gateway.overview(h.sa).accounts[0].suffixes == (('securities','5678'),)


def test_bound_upload_identity_checks_reused_under_current_directory(harness):
    h = harness
    h.state[0] = bind(h.state[0], A, h.a, 'securities','12345678',expected_revision=3)
    correct = RestoredUpload('history.csv','text/csv',b'Account Statement for 12345678 since 01/01/2025\n')
    wrong = RestoredUpload('history.csv','text/csv',b'Account Statement for 87654321 since 01/01/2025\n')
    with h.gateway.transaction(h.sa,h.a,write=True) as session:
        h.service._validate_upload(session,'thinkorswim_trade_history',correct)
        with pytest.raises(ValueError): h.service._validate_upload(session,'thinkorswim_trade_history',wrong)


def test_upload_transaction_cannot_write_artifacts_and_handles_expire(harness):
    h = harness
    with h.gateway.transaction(h.sa,h.a,write=True) as session:
        with pytest.raises(PermissionError): session.artifacts.write_text('publication.json','{}')
        with pytest.raises(PermissionError): session.artifacts.delete('publication.json')
    with pytest.raises(ClosedAccountSession): session.drafts.list_drafts()


def test_caught_sql_failure_cannot_commit_partial_operation(harness):
    h = harness
    with pytest.raises(AccountTransactionFailed):
        with h.gateway.transaction(h.sa,h.a,write=True):
            h.conn.info.transaction_status = TransactionStatus.INERROR


def test_view_grant_archive_and_revocation_enforced_on_each_transaction(harness):
    h = harness
    h.state[0] = h.state[0].set_grant(A,h.initial.users[1].id,h.a,Permission.VIEW,expected_revision=3)
    with h.gateway.transaction(h.sb,h.a) as session: assert not session._writable
    with pytest.raises(PermissionError):
        with h.gateway.transaction(h.sb,h.a,write=True): pass
    h.state[0] = h.state[0].set_grant(A,h.initial.users[1].id,h.a,None,expected_revision=4)
    with pytest.raises(PermissionError):
        with h.gateway.transaction(h.sb,h.a): pass
    h.state[0] = h.state[0].configure_account(A,h.a,archived=True,expected_revision=5)
    assert not h.gateway.overview(h.sa).accounts[0].writable
    with pytest.raises(PermissionError):
        with h.gateway.transaction(h.sa,h.a,write=True): pass


def test_synthetic_gateway_and_publication_contracts_remain_strict(harness):
    from campaigniq.account_import_preview import BoundAccountImportPreviewService
    from campaigniq.account_local_publication import publish_synthetic_month
    h = harness
    with pytest.raises(ValueError): BoundAccountImportPreviewService(h.gateway)
    with pytest.raises(ValueError): SignedInMonthlyInputs(object.__new__(LocalAccountDataGateway))
    with pytest.raises(ValueError): publish_synthetic_month(h.service,h.sa,h.a,uuid4(),0,authorize_synthetic_publication=True)


def test_directory_share_lock_precedes_financial_schema_access(harness):
    h = harness
    with h.gateway.transaction(h.sa,h.a): pass
    assert 'FOR SHARE' in h.conn.queries[0][0]
    assert h.conn.queries[0][1] == (h.store.registry_id,)
