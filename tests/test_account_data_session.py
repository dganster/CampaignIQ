"""Account storage boundary tests; real PostgreSQL checks run separately."""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.pq import TransactionStatus

from campaigniq.account_administration import AuthorizedAccount, Identity, LegacyOwner, Permission, propose_legacy_directory
from campaigniq.persistence.account_data_session import (
    AccountDataSession, AccountTransactionFailed, ClosedAccountSession, LocalAccountDataGateway,
)
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
from campaigniq.ui.account_administration_view import DEMO_ISSUER, demo_subject_prefix


class Connection:
    def __init__(self):
        self.info = SimpleNamespace(transaction_status=TransactionStatus.INTRANS)
        self.queries = []
        self.row = None
        self.commits = 0
        self.rollbacks = 0
        self.relations = [('campaigniq_accounts_dev', 'artifact_storage_dev'),
                          ('campaigniq_accounts_dev', 'import_draft_documents'),
                          ('campaigniq_accounts_dev', 'import_drafts')]

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        self.last_query = sql
        return self

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.relations if 'FROM pg_class' in self.last_query else []


@pytest.fixture
def session():
    conn = Connection()
    account = AuthorizedAccount(str(uuid4()), 'workspace-synthetic', 'workspace-synthetic', True)
    return AccountDataSession(conn, account, True, str(uuid4()))


@pytest.mark.parametrize('operation', ['write_artifact', 'delete_artifact', 'create_draft', 'upload', 'abandon'])
def test_read_only_session_rejects_all_mutations_before_sql(session, operation):
    session._writable = False
    with pytest.raises(PermissionError):
        if operation == 'write_artifact': session.artifacts.write_text('test.json', '{}')
        elif operation == 'delete_artifact': session.artifacts.delete('test.json')
        elif operation == 'create_draft': session.drafts.create_or_resume(2025, 1)
        elif operation == 'upload': session.drafts.upload(uuid4(), 'broker', 'test.csv', 'text/csv', b'synthetic')
        else: session.drafts.abandon(uuid4())
    assert session._connection.queries == []


@pytest.mark.parametrize('operation', ['read_artifact', 'list_keys', 'list_drafts', 'get_document', 'write'])
def test_closed_handles_cannot_be_reused(session, operation):
    session._active = False
    with pytest.raises(ClosedAccountSession):
        if operation == 'read_artifact': session.artifacts.read_text('test.json')
        elif operation == 'list_keys': session.artifacts.list_keys()
        elif operation == 'list_drafts': session.drafts.list_drafts()
        elif operation == 'get_document': session.drafts.document(uuid4(), 'broker')
        else: session.artifacts.write_text('test.json', '{}')
    assert session._connection.queries == []


def test_namespaces_are_registry_and_account_specific_and_read_only(session):
    assert session.artifacts.namespace == session.drafts.workspace
    assert len(session.artifacts.namespace) <= 128
    assert session.account.storage_scope == 'workspace-synthetic'
    other = AccountDataSession(Connection(), session.account, True, str(uuid4()))
    assert other.artifacts.namespace != session.artifacts.namespace
    with pytest.raises(AttributeError): session.artifacts.namespace = 'other'
    with pytest.raises(AttributeError): session.drafts.workspace = 'other'
    with pytest.raises(AttributeError): session.account = other.account


def test_existing_adapters_use_the_same_connection_without_nested_commits(session):
    session.artifacts.write_text('2025-01-test.json', '{}')
    session.drafts.list_drafts()
    assert len(session._connection.queries) == 2
    assert session._connection.queries[0][1][0] == session.artifacts.namespace
    assert session._connection.queries[1][1][0] == session.drafts.workspace
    assert session._connection.commits == session._connection.rollbacks == 0


def test_caught_database_error_poisons_the_outer_transaction(session):
    session._connection.info.transaction_status = TransactionStatus.INERROR
    with pytest.raises(AccountTransactionFailed): session.artifacts.exists('test.json')
    with pytest.raises(AccountTransactionFailed): session._check()


def test_missing_artifact_is_a_normal_lookup_without_poisoning(session):
    with pytest.raises(FileNotFoundError): session.artifacts.read_text('missing.json')
    session._check()


def test_schema_initialization_is_not_a_user_storage_operation(session):
    with pytest.raises(PermissionError): session.artifacts.initialize()
    with pytest.raises(PermissionError): session.drafts.initialize()
    assert not session._connection.queries


@pytest.fixture
def gateway(monkeypatch):
    for name in ('RENDER', 'RENDER_SERVICE_ID', 'PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS'):
        monkeypatch.delenv(name, raising=False)
    registry = str(uuid4()); prefix = demo_subject_prefix(registry)
    actor = Identity(DEMO_ISSUER, prefix + 'admin'); member = Identity(DEMO_ISSUER, prefix + 'member')
    d = propose_legacy_directory((
        LegacyOwner(actor, 'workspace-admin-synthetic', 'Admin', 'Account A', 'Broker'),
        LegacyOwner(member, 'workspace-member-synthetic', 'Member', 'Account B', 'Broker'),
    ), actor)
    current = [d]
    store = LocalPostgresAccountDirectory('dbname=campaigniq_dev', registry, development_only=True)
    conn = Connection()
    @contextmanager
    def connect(**kwargs):
        try: yield conn
        except BaseException:
            conn.rollbacks += 1
            raise
        else: conn.commits += 1
    monkeypatch.setattr(store, '_connection', connect)
    monkeypatch.setattr(store, '_load', lambda connection, **kwargs: current[0])
    return LocalAccountDataGateway(store), current, conn, actor, member


def test_gateway_reauthorizes_on_each_transaction_and_retains_row_lock(gateway):
    service, current, conn, actor, member = gateway
    d = current[0]
    with service.transaction(actor, d.accounts[0].id, write=True) as s:
        s.artifacts.write_text('same-month.json', 'synthetic')
        assert conn.commits == 0
    assert conn.commits == 1
    assert 'FOR SHARE' in conn.queries[0][0]
    current[0] = d.configure_account(actor, d.accounts[0].id, archived=True, expected_revision=0)
    with pytest.raises(PermissionError):
        with service.transaction(actor, d.accounts[0].id, write=True): pytest.fail('Archived account authorized')


def test_admin_does_not_receive_another_owners_financial_storage(gateway):
    service, current, conn, actor, member = gateway
    with pytest.raises(PermissionError):
        with service.transaction(actor, current[0].accounts[1].id): pytest.fail('Cross-account access')
    assert not any('SET LOCAL search_path' in query for query, _ in conn.queries)


def test_revoked_grant_cannot_be_reused_from_an_old_context(gateway):
    service, current, conn, actor, member = gateway
    d = current[0]
    current[0] = d.set_grant(actor, d.users[1].id, d.accounts[0].id, Permission.EDIT, expected_revision=0)
    with service.transaction(member, d.accounts[0].id, write=True) as old:
        assert old.account.account_id == d.accounts[0].id
    current[0] = current[0].set_grant(actor, d.users[1].id, d.accounts[0].id, None, expected_revision=1)
    with pytest.raises(ClosedAccountSession): old.artifacts.exists('test.json')
    with pytest.raises(PermissionError):
        with service.transaction(member, d.accounts[0].id): pytest.fail('Revoked account authorized')


def test_gateway_exception_rolls_back_and_closes_handles(gateway):
    service, current, conn, actor, member = gateway
    with pytest.raises(RuntimeError):
        with service.transaction(actor, current[0].accounts[0].id, write=True) as s:
            s.artifacts.write_text('test.json', '{}')
            raise RuntimeError('Injected failure')
    assert conn.rollbacks == 1 and conn.commits == 0
    with pytest.raises(ClosedAccountSession): s.drafts.list_drafts()


def test_missing_or_wrong_schema_cannot_fall_back_to_public_tables(gateway):
    service, current, conn, actor, member = gateway
    conn.relations = [('public', 'artifact_storage_dev'), ('public', 'import_draft_documents'), ('public', 'import_drafts')]
    with pytest.raises(ValueError):
        with service.transaction(actor, current[0].accounts[0].id): pytest.fail('Public schema allowed')


def test_real_identity_registry_is_rejected_before_financial_schema_access(gateway):
    from dataclasses import replace
    service, current, conn, actor, member = gateway
    d = current[0]
    real = Identity('https://accounts.google.com', 'real-subject')
    current[0] = replace(d, users=(replace(d.users[0], identity=real), d.users[1]))
    with pytest.raises(ValueError):
        with service.transaction(real, d.accounts[0].id): pytest.fail('Real identity allowed')
    assert not any('SET LOCAL search_path' in query for query, _ in conn.queries)


@pytest.mark.parametrize('mode', ['true', 1, None])
def test_transaction_mode_must_not_be_coerced(gateway, mode):
    service, current, conn, actor, member = gateway
    with pytest.raises(ValueError):
        with service.transaction(actor, current[0].accounts[0].id, write=mode): pytest.fail('Invalid mode allowed')
    assert not conn.queries


def test_forged_directory_context_cannot_construct_gateway():
    with pytest.raises(ValueError): LocalAccountDataGateway(SimpleNamespace(registry_id=str(uuid4())))
