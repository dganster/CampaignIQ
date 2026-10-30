"""Personal deployment display choices; no startup DDL or financial writes."""
from contextlib import contextmanager
from campaigniq.account_preferences import document,SavedPreferences,LocalAccountPreferences
from campaigniq.account_preference_roles import validate_preference_role
from campaigniq.persistence.account_directory_codec import checksum
from campaigniq.persistence.deployment_account_directory import PostgresAccountDirectory,DirectoryTransaction,require_transaction
from campaigniq.ui.preferences import validate_preferences

TABLE='campaigniq_accounts.account_preferences'


class DeploymentAccountPreferences:
    def __init__(self,store):
        if type(store) is not PostgresAccountDirectory:raise ValueError('Explicit deployment directory required')
        self.store=store

    @contextmanager
    def _authorized(self,user,account_id,*,readonly):
        with self.store._connection(readonly=readonly) as conn:
            validate_preference_role(conn)
            if not readonly:
                # Serializes with permission changes through the write and commit.
                conn.execute('SELECT registry_id FROM campaigniq_accounts.directories WHERE registry_id=%s FOR SHARE',(self.store.registry_id,))
            directory,actor=DirectoryTransaction(conn,self.store.settings)._actor(user)
            directory.authorize(actor.identity,account_id)
            yield conn,(self.store.registry_id,actor.user_id,account_id)
            require_transaction(conn)

    def load(self,user,account_id):
        with self._authorized(user,account_id,readonly=True) as (conn,key):
            revision,values=LocalAccountPreferences._read(conn,key,table=TABLE)
            return SavedPreferences(revision,tuple(sorted((values or {}).items())),True)

    def save(self,user,account_id,values,*,expected_revision):
        if type(expected_revision) is not int or expected_revision<0:raise ValueError('Displayed preference revision required')
        payload=document(values)
        from psycopg.types.json import Jsonb
        with self._authorized(user,account_id,readonly=False) as (conn,key):
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(':'.join(key),))
            revision,_=LocalAccountPreferences._read(conn,key,table=TABLE)
            if revision!=expected_revision:raise ValueError('Preferences changed; reload saved preferences')
            result=conn.execute('INSERT INTO '+TABLE+
                ' (registry_id,user_id,account_id,revision,document,document_sha256) VALUES(%s,%s,%s,%s,%s,%s) '
                'ON CONFLICT(registry_id,user_id,account_id) DO UPDATE SET revision=EXCLUDED.revision,'
                'document=EXCLUDED.document,document_sha256=EXCLUDED.document_sha256,updated_at=now()',
                (*key,revision+1,Jsonb(payload),checksum(payload)))
            if result.rowcount!=1 or LocalAccountPreferences._read(conn,key,table=TABLE)!=(revision+1,validate_preferences(payload)):
                raise ValueError('Preference write verification failed; transaction must roll back')
            require_transaction(conn);return revision+1
