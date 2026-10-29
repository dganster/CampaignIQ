"""Personal LOCAL display preferences, separate from every financial table."""
from dataclasses import dataclass
from types import MappingProxyType

from campaigniq.account_administration import Identity
from campaigniq.account_signin import signed_in_accounts, verify_legacy_workspace_bindings
from campaigniq.persistence.account_directory_codec import checksum
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
from campaigniq.ui.preferences import DEFAULT_PREFERENCES, validate_preferences

TABLE = 'campaigniq_preferences_dev.account_preferences'


class PreferenceIntegrityError(ValueError):
    pass


def document(values):
    if type(values) is not dict or set(values) != set(DEFAULT_PREFERENCES):
        raise ValueError('Exactly the supported display preferences are required')
    return {'version':1, **validate_preferences({'version':1, **values})}


@dataclass(frozen=True)
class SavedPreferences:
    revision: int
    values: tuple[tuple[str, object], ...]
    storage_ready: bool


class LocalAccountPreferences:
    def __init__(self, store, *, trusted_issuer, authorization):
        if type(store) is not LocalPostgresAccountDirectory:
            raise ValueError('Guarded local directory required')
        Identity(trusted_issuer,'configuration-validation')
        self.store=store;self.issuer=trusted_issuer
        self.authorization=MappingProxyType(dict(authorization))

    def _actor(self, conn, user, *, lock=False):
        if lock:
            conn.execute('SELECT registry_id FROM campaigniq_admin_dev.directories WHERE registry_id=%s FOR SHARE',
                (self.store.registry_id,))
        directory=self.store._load(conn)
        verify_legacy_workspace_bindings(directory,self.authorization,trusted_issuer=self.issuer)
        actor=signed_in_accounts(user,directory,trusted_issuer=self.issuer)
        return directory,actor

    @staticmethod
    def _ready(conn):
        row=conn.execute("SELECT to_regclass('campaigniq_preferences_dev.account_preferences')").fetchone()
        return row is not None and row[0] is not None

    def initialize(self,user):
        with self.store._connection() as conn:
            _,actor=self._actor(conn,user,lock=True)
            if not actor.administrator:raise PermissionError('Administrator must enable local preference storage')
            conn.execute('CREATE SCHEMA IF NOT EXISTS campaigniq_preferences_dev')
            conn.execute('''CREATE TABLE IF NOT EXISTS campaigniq_preferences_dev.account_preferences (
                registry_id uuid NOT NULL REFERENCES campaigniq_admin_dev.directories(registry_id),
                user_id uuid NOT NULL, account_id uuid NOT NULL,
                revision bigint NOT NULL CHECK(revision>0),
                document jsonb NOT NULL, document_sha256 text NOT NULL CHECK(length(document_sha256)=64),
                updated_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY(registry_id,user_id,account_id))''')

    @staticmethod
    def _read(conn,key):
        row=conn.execute('SELECT revision,document,document_sha256 FROM '+TABLE+
            ' WHERE registry_id=%s AND user_id=%s AND account_id=%s',key).fetchone()
        if row is None:return 0,None
        revision,payload,digest=row
        if (type(revision) is not int or revision<1 or type(payload) is not dict
            or set(payload)!={'version',*DEFAULT_PREFERENCES} or payload.get('version')!=1
            or type(payload.get('version')) is not int or checksum(payload)!=digest):
            raise PreferenceIntegrityError('Saved display preferences failed integrity checks')
        try:
            return revision,validate_preferences(payload)
        except ValueError as error:
            raise PreferenceIntegrityError('Saved display preference values are invalid') from error

    def load(self,user,account_id):
        with self.store._connection(readonly=True) as conn:
            directory,actor=self._actor(conn,user)
            directory.authorize(actor.identity,account_id)
            if not self._ready(conn):return SavedPreferences(0,(),False)
            rev,values=self._read(conn,(self.store.registry_id,actor.user_id,account_id))
            return SavedPreferences(rev,tuple(sorted((values or {}).items())),True)

    def save(self,user,account_id,values,*,expected_revision):
        if type(expected_revision) is not int or expected_revision<0:
            raise ValueError('Displayed preference revision required')
        payload=document(values)
        from psycopg.types.json import Jsonb
        with self.store._connection() as conn:
            directory,actor=self._actor(conn,user,lock=True)
            # VIEW grants and archived accounts may change their own display
            # choices. This does not confer financial write access.
            directory.authorize(actor.identity,account_id)
            if not self._ready(conn):raise ValueError('Local preference storage has not been enabled')
            key=(self.store.registry_id,actor.user_id,account_id)
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(':'.join(key),))
            revision,_=self._read(conn,key)
            if revision!=expected_revision:raise ValueError('Preferences changed; reload saved preferences')
            result=conn.execute('INSERT INTO '+TABLE+
                ' (registry_id,user_id,account_id,revision,document,document_sha256) VALUES(%s,%s,%s,%s,%s,%s) '
                'ON CONFLICT(registry_id,user_id,account_id) DO UPDATE SET revision=EXCLUDED.revision,'
                'document=EXCLUDED.document,document_sha256=EXCLUDED.document_sha256,updated_at=now()',
                (*key,revision+1,Jsonb(payload),checksum(payload)))
            if result.rowcount!=1 or self._read(conn,key)!=(revision+1,validate_preferences(payload)):
                raise ValueError('Preference write verification failed; transaction must roll back')
            return revision+1
