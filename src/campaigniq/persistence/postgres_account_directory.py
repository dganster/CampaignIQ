"""Opt-in LOCAL PostgreSQL directory service; not wired to the live application.

Uses only campaigniq_dev over the standard local Unix socket. No connection,
DDL or schema initialization occurs on import or construction. All commands
load and authorize against the locked current snapshot, then atomically append
one audit event and replace the directory with a revision compare-and-swap.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
import pwd
from uuid import UUID

from campaigniq.account_administration import Directory, Identity, Permission
from campaigniq.persistence.account_directory_codec import (
    checksum, decode, encode, event_document,
)


SCHEMA = 'campaigniq_admin_dev'
ALLOWED_COMMANDS = {
    'add_user': {'identity', 'name'},
    'configure_user': {'user_id', 'name', 'enabled', 'admin'},
    'add_account': {'owner_id', 'name', 'broker'},
    'configure_account': {'account_id', 'name', 'archived'},
    'set_grant': {'user_id', 'account_id', 'permission'},
}


def local_conninfo(dsn: str, *, development_only: bool) -> str:
    if development_only is not True or os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):
        raise ValueError('Explicit local development mode required')
    if any(os.environ.get(k) for k in ('PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS')):
        raise ValueError('PostgreSQL routing overrides are blocked')
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    try:
        values = conninfo_to_dict(dsn)
    except Exception:
        raise ValueError('Invalid local development connection settings') from None
    allowed = {'host', 'port', 'dbname', 'user', 'password', 'connect_timeout', 'application_name', 'sslmode'}
    user = pwd.getpwuid(os.getuid()).pw_name
    if (set(values) - allowed or values.get('dbname') != 'campaigniq_dev'
            or values.get('host', '/var/run/postgresql') != '/var/run/postgresql'
            or values.get('port', '5432') != '5432' or values.get('user', user) != user):
        raise ValueError('Only campaigniq_dev with the current OS user on /var/run/postgresql:5432 is allowed')
    values.update(host='/var/run/postgresql', port='5432', dbname='campaigniq_dev', user=user,
                  connect_timeout='5', application_name='campaigniq_admin_development')
    return make_conninfo(**values)


class LocalPostgresAccountDirectory:
    def __init__(self, dsn: str, registry_id: str, *, development_only: bool):
        if not isinstance(registry_id, str) or str(UUID(registry_id)) != registry_id:
            raise ValueError('Canonical registry UUID required')
        self._conninfo = local_conninfo(dsn, development_only=development_only)
        self.registry_id = registry_id

    @contextmanager
    def _connection(self, *, readonly=False):
        import psycopg
        # Revalidate before every connection, including after environment changes.
        conninfo = local_conninfo(self._conninfo, development_only=True)
        with psycopg.connect(conninfo) as conn:
            if readonly:
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            conn.execute("SET LOCAL lock_timeout = '5s'")
            conn.execute("SET LOCAL statement_timeout = '20s'")
            row = conn.execute('SELECT current_database(), current_user, inet_server_addr()').fetchone()
            if row != ('campaigniq_dev', pwd.getpwuid(os.getuid()).pw_name, None):
                raise ValueError('Connected server is not the required local development database')
            yield conn

    def initialize(self):
        with self._connection() as conn:
            conn.execute('CREATE SCHEMA IF NOT EXISTS campaigniq_admin_dev')
            conn.execute('''CREATE TABLE IF NOT EXISTS campaigniq_admin_dev.directories (
                registry_id uuid PRIMARY KEY,
                revision bigint NOT NULL CHECK (revision >= 0),
                document jsonb NOT NULL,
                document_sha256 text NOT NULL CHECK (length(document_sha256) = 64),
                updated_at timestamptz NOT NULL DEFAULT now()
            )''')
            conn.execute('''CREATE TABLE IF NOT EXISTS campaigniq_admin_dev.audit_events (
                registry_id uuid NOT NULL REFERENCES campaigniq_admin_dev.directories(registry_id),
                sequence bigint NOT NULL CHECK (sequence > 0),
                event jsonb NOT NULL,
                event_sha256 text NOT NULL CHECK (length(event_sha256) = 64),
                recorded_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (registry_id, sequence)
            )''')

    def _load(self, conn, *, lock=False) -> Directory:
        row = conn.execute('''SELECT revision, document, document_sha256
            FROM campaigniq_admin_dev.directories WHERE registry_id=%s''' + (' FOR UPDATE' if lock else ''),
                           (self.registry_id,)).fetchone()
        if row is None:
            raise ValueError('Directory has not been explicitly bootstrapped')
        revision, document, saved_hash = row
        if checksum(document) != saved_hash:
            raise ValueError('Directory content checksum differs')
        directory = decode(document)
        if directory.revision != revision:
            raise ValueError('Directory revision differs from its event history')
        audit = conn.execute('''SELECT sequence,event,event_sha256 FROM campaigniq_admin_dev.audit_events
            WHERE registry_id=%s ORDER BY sequence''', (self.registry_id,)).fetchall()
        expected = [(e.sequence, event_document(e), checksum(event_document(e))) for e in directory.events]
        if audit != expected:
            raise ValueError('Directory audit ledger differs; access is blocked')
        return directory

    def bootstrap(self, initial: Directory):
        """Explicit offline bootstrap only; never auto-create from a browser login."""
        from psycopg.types.json import Jsonb
        if not isinstance(initial, Directory) or initial.revision != 0:
            raise ValueError('A reviewed initial directory without events is required')
        document = encode(initial)
        with self._connection() as conn:
            conn.execute('''INSERT INTO campaigniq_admin_dev.directories
                (registry_id,revision,document,document_sha256) VALUES (%s,0,%s,%s)
                ON CONFLICT (registry_id) DO NOTHING''',
                         (self.registry_id, Jsonb(document), checksum(document)))
            if self._load(conn, lock=True) != initial:
                raise ValueError('Existing directory differs; bootstrap cannot overwrite it')

    def _snapshot(self) -> Directory:
        """Internal trusted-service snapshot, not a browser-facing directory API."""
        with self._connection(readonly=True) as conn:
            # _connection establishes a consistent snapshot before any reads.
            return self._load(conn)

    def authorize(self, identity: Identity, account_id: str, *, write=False):
        return self._snapshot().authorize(identity, account_id, write=write)

    def select(self, identity: Identity, account_ids: tuple[str, ...]):
        return self._snapshot().select(identity, account_ids)

    def activity(self, identity: Identity):
        directory = self._snapshot()
        if not directory.user(identity).admin:
            raise PermissionError('Administrator permission required')
        return directory.events

    def _append_event(self, conn, event):
        from psycopg.types.json import Jsonb
        document = event_document(event)
        conn.execute('''INSERT INTO campaigniq_admin_dev.audit_events
            (registry_id,sequence,event,event_sha256) VALUES (%s,%s,%s,%s)''',
                     (self.registry_id, event.sequence, Jsonb(document), checksum(document)))

    def execute(self, actor_identity: Identity, expected_revision: int, command: str, /, **arguments):
        from psycopg.types.json import Jsonb
        if not isinstance(command, str) or command not in ALLOWED_COMMANDS:
            raise ValueError('Unsupported administration command')
        if set(arguments) - ALLOWED_COMMANDS[command]:
            raise ValueError('Unsupported administration arguments')
        # Permission values are explicit enums, not untrusted truthy form input.
        if command == 'set_grant' and arguments.get('permission') is not None and not isinstance(arguments['permission'], Permission):
            raise ValueError('Explicit permission enum required')
        with self._connection() as conn:
            current = self._load(conn, lock=True)
            updated = getattr(current, command)(actor_identity, expected_revision=expected_revision, **arguments)
            if updated.revision != current.revision + 1 or updated.events[:-1] != current.events:
                raise ValueError('Invalid directory/audit transition')
            document = encode(updated)
            result = conn.execute('''UPDATE campaigniq_admin_dev.directories
                SET revision=%s,document=%s,document_sha256=%s,updated_at=now()
                WHERE registry_id=%s AND revision=%s''',
                (updated.revision, Jsonb(document), checksum(document), self.registry_id, current.revision))
            if result.rowcount != 1:
                raise ValueError('Directory revision changed')
            self._append_event(conn, updated.events[-1])
            if self._load(conn) != updated:
                raise ValueError('Directory write verification differs')
        return updated.revision
