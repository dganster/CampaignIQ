from contextlib import contextmanager
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from campaigniq.account_administration import Identity, LegacyOwner, Permission, propose_legacy_directory
from campaigniq.account_legacy_access import LocalLegacyAccountAccess
from campaigniq.account_legacy_access_rehearsal import RehearsalSession
from campaigniq.account_signin import verify_legacy_workspace_bindings
from campaigniq.persistence.account_data_session import ClosedAccountSession
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory

ISSUER = 'https://accounts.example.test'
A, B = Identity(ISSUER, 'a'), Identity(ISSUER, 'b')
MAPPING = {'a': 'workspace-a', 'b': 'workspace-b'}


class Result:
    def __init__(self, rows): self.rows = rows
    def fetchall(self): return self.rows
    def fetchone(self): return self.rows[0] if self.rows else None


class Connection:
    def __init__(self):
        from psycopg.pq import TransactionStatus
        self.info = SimpleNamespace(transaction_status=TransactionStatus.INTRANS)
        self.queries = []
        self.content = b'original bytes'
        self.digest = sha256(self.content).hexdigest()
        self.draft, self.doc = uuid4(), uuid4()
        self.bad_artifact = False
        self.bad_schema = False

    def execute(self, sql, params=()):
        normalized = ' '.join(sql.split())
        self.queries.append((normalized, params))
        if normalized.startswith('SET LOCAL search_path'): return Result([])
        if 'FROM pg_class' in normalized:
            return Result([('other' if self.bad_schema else 'public', n) for n in
                ('artifact_storage_dev', 'import_draft_documents', 'import_drafts')])
        if normalized.startswith('SELECT artifact_key,content'):
            return Result([('saved.json', '{}')])
        if normalized.startswith('SELECT artifact_key FROM'):
            return Result([('saved.json',)])
        if normalized.startswith('SELECT content FROM'):
            return Result([('different' if self.bad_artifact else '{}',)])
        if normalized.startswith('SELECT id,year,month,state,revision'):
            return Result([(self.draft, 2025, 1, 'draft', 1)])
        if normalized.startswith('SELECT id,workspace,year,month,revision'):
            return Result([(self.draft, params[0], 2025, 1, 1)])
        if normalized.startswith('SELECT d.id,d.draft_id'):
            return Result([(self.doc, self.draft, 'broker', 'source.csv', 'text/csv',
                self.digest, self.content, 1, False, 'draft')])
        if normalized.startswith('SELECT d.filename,d.media_type,d.content'):
            return Result([('source.csv', 'text/csv', self.content)])
        raise AssertionError('Unexpected SQL: ' + normalized)


@pytest.fixture
def harness():
    directory = propose_legacy_directory((LegacyOwner(A, 'workspace-a', 'A', 'Account A', 'schwab'),
        LegacyOwner(B, 'workspace-b', 'B', 'Account B', 'schwab')), A)
    conn = Connection()
    store = object.__new__(LocalPostgresAccountDirectory)
    store.registry_id = str(uuid4())
    current = [directory]
    modes = []
    @contextmanager
    def connection(*, readonly=False):
        modes.append(readonly)
        yield conn
    store._connection = connection
    store._load = lambda connection: current[0]
    reader = LocalLegacyAccountAccess(store, trusted_issuer=ISSUER, authorization=MAPPING)
    return SimpleNamespace(reader=reader, store=store, conn=conn, current=current,
        initial=directory, modes=modes, account=directory.accounts[0].id,
        other=directory.accounts[1].id, user=RehearsalSession(A))


def test_reader_routes_exact_original_namespace_and_reuses_existing_adapters(harness):
    h = harness
    result = h.reader.inventory(h.user, h.account)
    assert result.storage_scope == 'workspace-a' and result.readonly
    assert result.adapter_parity_verified and result.artifact_count == result.active_drafts == 1
    assert result.document_versions == 1 and result.original_bytes == len(h.conn.content)
    assert all(h.modes)
    assert all('workspace-a' in params or not params or h.conn.draft in params
               for _, params in h.conn.queries)
    assert not any(sql.startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE')) for sql, _ in h.conn.queries)


def test_owner_and_admin_have_no_implicit_cross_account_access(harness):
    h = harness
    assert [a.id for a in h.reader.choices(h.user)] == [h.account]
    with pytest.raises(PermissionError): h.reader.inventory(h.user, h.other)
    assert not any('public.artifact_storage_dev' in sql for sql, _ in h.conn.queries)


@pytest.mark.parametrize('identity,logged_in', [(Identity('https://other.test', 'a'), True), (A, False),
    (Identity(ISSUER, 'unregistered'), True)])
def test_invalid_signin_cannot_read_rows(harness, identity, logged_in):
    h = harness
    with pytest.raises(PermissionError): h.reader.inventory(RehearsalSession(identity, logged_in=logged_in), h.account)
    assert not h.conn.queries


def test_disabled_other_owner_preserves_mapping_but_has_no_access(harness):
    h = harness
    h.current[0] = h.initial.configure_user(A, h.initial.users[1].id, enabled=False, expected_revision=0)
    assert h.reader.inventory(h.user, h.account).adapter_parity_verified
    with pytest.raises(PermissionError): h.reader.choices(RehearsalSession(B))
    assert verify_legacy_workspace_bindings(h.current[0], MAPPING, trusted_issuer=ISSUER)


def test_grants_and_revocation_checked_fresh(harness):
    h = harness
    h.current[0] = h.initial.set_grant(A, h.initial.users[1].id, h.account, Permission.VIEW, expected_revision=0)
    assert h.reader.inventory(RehearsalSession(B), h.account).adapter_parity_verified
    h.current[0] = h.current[0].set_grant(A, h.initial.users[1].id, h.account, None, expected_revision=1)
    with pytest.raises(PermissionError): h.reader.inventory(RehearsalSession(B), h.account)


def test_archived_history_remains_readonly(harness):
    h = harness
    h.current[0] = h.initial.configure_account(A, h.account, archived=True, expected_revision=0)
    assert h.reader.choices(h.user)[0].archived
    with h.reader._session(h.user, h.account) as (_, session, _):
        with pytest.raises(PermissionError): session.artifacts.delete('saved.json')
        with pytest.raises(PermissionError): session.drafts.abandon(h.conn.draft)
    with pytest.raises(ClosedAccountSession): session.artifacts.list_keys()


@pytest.mark.parametrize('failure', ['digest', 'adapter', 'schema', 'mapping'])
def test_corruption_or_rebinding_cannot_pass_parity(harness, failure):
    h = harness
    if failure == 'digest': h.conn.digest = '0' * 64
    if failure == 'adapter': h.conn.bad_artifact = True
    if failure == 'schema': h.conn.bad_schema = True
    if failure == 'mapping':
        h.reader = LocalLegacyAccountAccess(h.store, trusted_issuer=ISSUER,
            authorization={'a':'workspace-b', 'b':'workspace-a'})
    with pytest.raises(ValueError): h.reader.inventory(h.user, h.account)


def test_mutable_server_mapping_is_copied(harness):
    mapping = dict(MAPPING)
    h = harness
    service = LocalLegacyAccountAccess(h.store, trusted_issuer=ISSUER, authorization=mapping)
    mapping['a'] = 'workspace-b'
    assert service.inventory(h.user, h.account).storage_scope == 'workspace-a'


def test_synthetic_account_cannot_be_routed_to_legacy_tables(harness):
    h = harness
    h.current[0] = h.initial.add_account(A, h.initial.users[0].id, 'New account', 'schwab', expected_revision=0)
    new = h.current[0].accounts[-1].id
    assert new not in [c.id for c in h.reader.choices(h.user)]
    with pytest.raises(ValueError): h.reader.inventory(h.user, new)


def test_directory_audit_failure_blocks_before_financial_reads(harness):
    h = harness
    h.store._load = Mock(side_effect=ValueError('audit differs'))
    with pytest.raises(ValueError): h.reader.inventory(h.user, h.account)
    assert not h.conn.queries
