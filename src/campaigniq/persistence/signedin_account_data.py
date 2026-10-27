"""LOCAL real-sign-in draft storage for NEW accounts only.

Legacy workspaces and synthetic preview partitions cannot be routed here.
Every operation reloads the audited directory under a lock held through the
data transaction. No publication or artifact mutation API is provided.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from campaigniq.account_administration import Identity
from campaigniq.account_reconciliation_preview import ReadOnlyArtifacts
from campaigniq.account_signin import signed_in_accounts, verify_legacy_workspace_bindings
from campaigniq.broker_account_binding import bind, bindings
from campaigniq.persistence.account_data_session import AccountDataSession
from campaigniq.persistence.account_directory_codec import checksum, encode
from campaigniq.persistence.import_drafts import SCHEMA as DRAFT_SCHEMA
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory

DATA_SCHEMA = 'campaigniq_signedin_accounts_dev'


def _new_schwab_account(directory, account_id):
    account = next((a for a in directory.accounts if a.id == account_id), None)
    if account is None or account.legacy:
        raise ValueError('Existing workspace data remains read-only; select a new brokerage account')
    if account.broker.strip().casefold() not in {'schwab', 'charles schwab'}:
        raise ValueError('Monthly input uploads currently support Schwab accounts only')
    return account


@dataclass(frozen=True)
class InputAccount:
    id: str
    name: str
    archived: bool
    writable: bool
    suffixes: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class InputAccounts:
    administrator: bool
    directory_revision: int
    accounts: tuple[InputAccount, ...]
    user_id: str


class LocalSignedInInputGateway:
    def __init__(self, directory_store, *, trusted_issuer, authorization):
        if type(directory_store) is not LocalPostgresAccountDirectory:
            raise ValueError('Guarded local directory service required')
        Identity(trusted_issuer, 'configuration-validation')
        self._directory = directory_store
        self._issuer = trusted_issuer
        self._authorization = MappingProxyType(dict(authorization))

    def _current(self, conn, user, *, lock=False):
        directory = self._directory._load(conn, lock=lock)
        verify_legacy_workspace_bindings(directory, self._authorization, trusted_issuer=self._issuer)
        return directory, signed_in_accounts(user, directory, trusted_issuer=self._issuer)

    def overview(self, user):
        with self._directory._connection(readonly=True) as conn:
            directory, actor = self._current(conn, user)
            available = []
            for account in directory.accounts:
                if account.legacy or account.id not in actor.account_ids:
                    continue
                access = directory.authorize(actor.identity, account.id)
                suffixes = tuple(sorted((kind, value.suffix) for kind, value in bindings(directory, account.id).items()))
                available.append(InputAccount(account.id, account.name, account.archived, access.writable, suffixes))
            return InputAccounts(actor.administrator, directory.revision, tuple(available), actor.user_id)

    @staticmethod
    def _ready(conn):
        rows = conn.execute('''SELECT n.nspname,c.relname FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.oid IN (to_regclass('campaigniq_signedin_accounts_dev.artifact_storage_dev'),
                           to_regclass('campaigniq_signedin_accounts_dev.import_drafts'),
                           to_regclass('campaigniq_signedin_accounts_dev.import_draft_documents'))
            ORDER BY c.relname''').fetchall()
        return rows == [(DATA_SCHEMA, name) for name in ('artifact_storage_dev', 'import_draft_documents', 'import_drafts')]

    def storage_ready(self, user):
        with self._directory._connection(readonly=True) as conn:
            self._current(conn, user)
            return self._ready(conn)

    def initialize(self, user):
        with self._directory._connection() as conn:
            _, actor = self._current(conn, user, lock=True)
            if not actor.administrator:
                raise PermissionError('Administrator must initialize local input storage')
            conn.execute('CREATE SCHEMA IF NOT EXISTS campaigniq_signedin_accounts_dev')
            conn.execute('SET LOCAL search_path=campaigniq_signedin_accounts_dev,pg_catalog')
            conn.execute('''CREATE TABLE IF NOT EXISTS campaigniq_signedin_accounts_dev.artifact_storage_dev (
                namespace text NOT NULL, artifact_key text NOT NULL, content text NOT NULL,
                updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(namespace,artifact_key))''')
            for statement in DRAFT_SCHEMA.split(';'):
                if statement.strip():
                    conn.execute(statement)
            if not self._ready(conn):
                raise ValueError('Dedicated local input schema could not be verified')

    def bind(self, user, account_id, kind, number, *, expected_revision, confirm_replace=False):
        from psycopg.types.json import Jsonb
        with self._directory._connection() as conn:
            current, actor = self._current(conn, user, lock=True)
            _new_schwab_account(current, account_id)
            updated = bind(current, actor.identity, account_id, kind, number,
                expected_revision=expected_revision, confirm_replace=confirm_replace)
            if updated == current:
                return current.revision
            document = encode(updated)
            result = conn.execute('''UPDATE campaigniq_admin_dev.directories
                SET revision=%s,document=%s,document_sha256=%s,updated_at=now()
                WHERE registry_id=%s AND revision=%s''',
                (updated.revision, Jsonb(document), checksum(document), self._directory.registry_id, current.revision))
            if result.rowcount != 1:
                raise ValueError('Directory changed; reload before binding')
            self._directory._append_event(conn, updated.events[-1])
            if self._directory._load(conn) != updated:
                raise ValueError('Broker binding write verification differs')
            return updated.revision

    @contextmanager
    def transaction(self, user, account_id, *, write=False):
        if type(write) is not bool:
            raise ValueError('Explicit boolean transaction mode required')
        session = None
        with self._directory._connection() as conn:
            try:
                conn.execute('''SELECT registry_id FROM campaigniq_admin_dev.directories
                    WHERE registry_id=%s FOR SHARE''', (self._directory.registry_id,))
                directory, actor = self._current(conn, user)
                access = directory.authorize(actor.identity, account_id, write=write)
                _new_schwab_account(directory, account_id)
                if not self._ready(conn):
                    raise ValueError('An administrator must initialize the dedicated local input storage')
                conn.execute('SET LOCAL search_path=campaigniq_signedin_accounts_dev,pg_catalog')
                session = AccountDataSession(conn, access, write, self._directory.registry_id)
                session._physical_scope = 'signedin-' + UUID(self._directory.registry_id).hex + '-' + UUID(account_id).hex
                # Even an upload transaction cannot publish or replace artifacts.
                session.artifacts = ReadOnlyArtifacts(session.artifacts)
                yield session
                session._check()
            finally:
                if session is not None:
                    session._active = False
