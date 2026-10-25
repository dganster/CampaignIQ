"""Transaction-bound account artifacts and import drafts for LOCAL synthetic tests.

Not a publication endpoint or live runtime switch. The existing adapters run on
one authorized connection, so they cannot commit fragments independently.
"""
from __future__ import annotations

from contextlib import contextmanager
from uuid import UUID

from campaigniq.persistence.import_drafts import ImportDraftRepository, SCHEMA
from campaigniq.persistence.postgres_artifact_storage import PostgresArtifactStorage
from campaigniq.ui.account_administration_view import validate_demo_directory


DATA_SCHEMA = 'campaigniq_accounts_dev'


class ClosedAccountSession(RuntimeError):
    pass


class AccountTransactionFailed(RuntimeError):
    pass


class _Artifacts(PostgresArtifactStorage):
    def __init__(self, session):
        self._session = session

    @property
    def namespace(self):
        return self._session._physical_scope

    def _connect(self):
        return self._session._operation()

    def initialize(self):
        raise PermissionError('Schema initialization is not an account operation')

    def write_text(self, key, content):
        self._session._check(write=True)
        return super().write_text(key, content)

    def delete(self, key):
        self._session._check(write=True)
        return super().delete(key)


class _Drafts(ImportDraftRepository):
    def __init__(self, session):
        self._session = session

    @property
    def workspace(self):
        return self._session._physical_scope

    def _connect(self):
        return self._session._operation()

    def initialize(self):
        raise PermissionError('Schema initialization is not an account operation')

    def create_or_resume(self, year, month):
        self._session._check(write=True)
        return super().create_or_resume(year, month)

    def upload(self, draft_id, role, filename, media_type, content, *, replace=False):
        self._session._check(write=True)
        return super().upload(draft_id, role, filename, media_type, content, replace=replace)

    def abandon(self, draft_id):
        self._session._check(write=True)
        return super().abandon(draft_id)


class AccountDataSession:
    """Server-only short-lived handle. Never expose a raw connection in a UI API."""
    def __init__(self, connection, account, writable, registry_id):
        self._connection = connection
        self._account = account
        self._writable = writable
        self._physical_scope = 'preview-' + UUID(registry_id).hex + '-' + UUID(account.account_id).hex
        self._active = True
        self._failed = False
        self.artifacts = _Artifacts(self)
        self.drafts = _Drafts(self)

    @property
    def account(self):
        return self._account

    def _check(self, *, write=False):
        from psycopg.pq import TransactionStatus
        if not self._active:
            raise ClosedAccountSession('Account session has ended; obtain fresh authorization')
        if self._failed or self._connection.info.transaction_status == TransactionStatus.INERROR:
            self._failed = True
            raise AccountTransactionFailed('Account transaction failed and must roll back')
        if write and not self._writable:
            raise PermissionError('Account session is read-only')

    @contextmanager
    def _operation(self):
        from psycopg.pq import TransactionStatus
        self._check()
        try:
            yield self._connection
        finally:
            if self._connection.info.transaction_status == TransactionStatus.INERROR:
                self._failed = True


class LocalAccountDataGateway:
    def __init__(self, directory_store):
        # Requires the actual guarded local directory service, not a browser
        # account-context object, DSN supplied by an account selector, or cache.
        from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
        if type(directory_store) is not LocalPostgresAccountDirectory:
            raise ValueError('A guarded local directory service is required')
        self._directory = directory_store

    def initialize(self):
        # Check synthetic registry ownership before creating the separate schema.
        with self._directory._connection() as conn:
            directory = self._directory._load(conn, lock=True)
            validate_demo_directory(directory, self._directory.registry_id)
            conn.execute('CREATE SCHEMA IF NOT EXISTS campaigniq_accounts_dev')
            conn.execute('SET LOCAL search_path = campaigniq_accounts_dev, pg_catalog')
            conn.execute('''CREATE TABLE IF NOT EXISTS campaigniq_accounts_dev.artifact_storage_dev (
                namespace text NOT NULL, artifact_key text NOT NULL, content text NOT NULL,
                updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(namespace,artifact_key)
            )''')
            for statement in SCHEMA.split(';'):
                if statement.strip():
                    conn.execute(statement)

    @contextmanager
    def transaction(self, identity, account_id, *, write=False):
        if type(write) is not bool:
            raise ValueError('Explicit boolean transaction mode required')
        session = None
        with self._directory._connection() as conn:
            try:
                # SHARE conflicts with the directory's administrative UPDATE
                # lock. A revoke cannot commit in the middle of this operation.
                conn.execute('''SELECT registry_id FROM campaigniq_admin_dev.directories
                    WHERE registry_id=%s FOR SHARE''', (self._directory.registry_id,))
                directory = self._directory._load(conn)
                validate_demo_directory(directory, self._directory.registry_id)
                account = directory.authorize(identity, account_id, write=write)
                conn.execute('SET LOCAL search_path = campaigniq_accounts_dev, pg_catalog')
                # Never fall back to public tables if isolated schema setup was
                # forgotten. Both relations must resolve to our dedicated schema.
                rows = conn.execute('''SELECT n.nspname,c.relname FROM pg_class c
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE c.oid IN (to_regclass('artifact_storage_dev'),to_regclass('import_drafts'),
                                   to_regclass('import_draft_documents')) ORDER BY c.relname''').fetchall()
                if rows != [(DATA_SCHEMA, 'artifact_storage_dev'), (DATA_SCHEMA, 'import_draft_documents'), (DATA_SCHEMA, 'import_drafts')]:
                    raise ValueError('Initialize the isolated account-data schema first')
                session = AccountDataSession(conn, account, write, self._directory.registry_id)
                yield session
                session._check()
            finally:
                if session is not None:
                    session._active = False
