"""Separate deployment directory backend; explicit config, no startup DDL or bootstrap.

The public methods accept only the application's server-owned sign-in object.
This backend does not yet route financial data or activate the deployed UI.
"""
from contextlib import contextmanager
from campaigniq.account_administration import Identity, Permission
from campaigniq.account_backend_config import AccountDatabaseTarget, AccountDirectorySettings
from campaigniq.account_signin import signed_in_accounts,verify_legacy_workspace_bindings
from campaigniq.account_schema_migrations import verify_schema
from campaigniq.persistence.account_directory_codec import checksum,decode,encode,event_document
from campaigniq.persistence.postgres_account_directory import ALLOWED_COMMANDS


def require_transaction(conn):
    from psycopg.pq import TransactionStatus
    if conn.info.transaction_status!=TransactionStatus.INTRANS:
        raise ValueError('Active transaction required; handle has expired or failed')


def validate_runtime_role(conn,target):
    row=conn.execute('''SELECT current_database(),current_user,session_user,
        r.rolsuper,r.rolcreatedb,r.rolcreaterole,r.rolbypassrls,
        has_database_privilege(current_user,current_database(),'CREATE'),
        has_schema_privilege(current_user,'campaigniq_accounts','CREATE'),
        pg_has_role(current_user,n.nspowner,'MEMBER'),
        EXISTS(SELECT 1 FROM pg_class c WHERE c.relnamespace=n.oid AND pg_has_role(current_user,c.relowner,'MEMBER'))
        FROM pg_roles r CROSS JOIN pg_namespace n
        WHERE r.rolname=current_user AND n.nspname='campaigniq_accounts' ''').fetchone()
    if row!=(target.database,target.user,target.user,False,False,False,False,False,False,False,False):
        raise PermissionError('Account runtime target or least privilege role differs')

    # Runtime can update the one audited directory, never bootstrap another one,
    # rewrite audit history, mutate the migration ledger or truncate metadata.
    from campaigniq.account_table_policy_checks import validate_tables
    validate_tables(conn,tuple(('campaigniq_accounts',table,allowed) for table,allowed in (
        ('directories',{'SELECT','UPDATE'}),('audit_events',{'SELECT','INSERT'}),('schema_migrations',{'SELECT'}))))



class DirectoryTransaction:
    """Internal transaction implementation shared by runtime and restore rehearsal.

    Caller owns connection/transaction, propagates every failure, and never exposes
    this object as a browser capability. Methods do not commit or initialize DDL.
    """
    def __init__(self,conn,settings):
        if type(settings) is not AccountDirectorySettings:raise ValueError('Trusted settings required')
        require_transaction(conn)
        self.conn=conn;self.settings=settings

    def _load(self,*,lock=False):
        require_transaction(self.conn)
        registries=self.conn.execute('SELECT registry_id::text FROM campaigniq_accounts.directories ORDER BY registry_id').fetchall()
        if registries!=[(self.settings.registry_id,)]:raise ValueError('Explicit single deployment registry required')
        row=self.conn.execute('''SELECT revision,document,document_sha256
            FROM campaigniq_accounts.directories WHERE registry_id=%s'''+(' FOR UPDATE' if lock else ''),
            (self.settings.registry_id,)).fetchone()
        if row is None or checksum(row[1])!=row[2]:raise ValueError('Directory checksum differs')
        directory=decode(row[1])
        if directory.revision!=row[0]:raise ValueError('Directory revision differs')
        audit=self.conn.execute('''SELECT sequence,event,event_sha256 FROM campaigniq_accounts.audit_events
            WHERE registry_id=%s ORDER BY sequence''',(self.settings.registry_id,)).fetchall()
        if audit!=[(e.sequence,event_document(e),checksum(event_document(e))) for e in directory.events]:
            raise ValueError('Directory audit differs; access blocked')
        verify_legacy_workspace_bindings(directory,self.settings.authorization,trusted_issuer=self.settings.trusted_issuer)
        if any(u.identity.issuer!=self.settings.trusted_issuer for u in directory.users):
            raise ValueError('Directory identity issuer differs')
        return directory

    def _actor(self,user,*,lock=False):
        directory=self._load(lock=lock)
        actor=signed_in_accounts(user,directory,trusted_issuer=self.settings.trusted_issuer)
        return directory,actor

    def session(self,user):return self._actor(user)[1]

    def snapshot(self,user):
        directory,actor=self._actor(user)
        if not actor.administrator:raise PermissionError('Administrator permission required')
        return directory

    def authorize(self,user,account_id,*,write=False):
        directory,actor=self._actor(user)
        return directory.authorize(actor.identity,account_id,write=write)

    def execute(self,user,expected_revision,command,/,**arguments):
        if type(command) is not str or command not in ALLOWED_COMMANDS or set(arguments)-ALLOWED_COMMANDS[command]:
            raise ValueError('Unsupported administration command or arguments')
        if command=='set_grant' and arguments.get('permission') is not None and type(arguments['permission']) is not Permission:
            raise ValueError('Explicit permission enum required')
        if command=='add_user':
            identity=arguments.get('identity')
            if type(identity) is not Identity or identity.issuer!=self.settings.trusted_issuer:
                raise ValueError('Configured identity issuer required')
        directory,actor=self._actor(user,lock=True)
        updated=getattr(directory,command)(actor.identity,expected_revision=expected_revision,**arguments)
        return self._persist_transition(directory,updated)

    def _persist_transition(self,current,updated):
        if updated.revision!=current.revision+1 or updated.events[:-1]!=current.events:
            raise ValueError('Invalid directory transition')
        verify_legacy_workspace_bindings(updated,self.settings.authorization,trusted_issuer=self.settings.trusted_issuer)
        from psycopg.types.json import Jsonb
        document=encode(updated)
        result=self.conn.execute('''UPDATE campaigniq_accounts.directories
            SET revision=%s,document=%s,document_sha256=%s,updated_at=now()
            WHERE registry_id=%s AND revision=%s''',
            (updated.revision,Jsonb(document),checksum(document),self.settings.registry_id,current.revision))
        if result.rowcount!=1:raise ValueError('Directory changed; reload')
        self._append_event(updated.events[-1])
        if self._load()!=updated:raise ValueError('Directory write verification differs')
        return updated.revision

    def _append_event(self,event):
        from psycopg.types.json import Jsonb
        document=event_document(event)
        self.conn.execute('''INSERT INTO campaigniq_accounts.audit_events
            (registry_id,sequence,event,event_sha256) VALUES(%s,%s,%s,%s)''',
            (self.settings.registry_id,event.sequence,Jsonb(document),checksum(document)))


class PostgresAccountDirectory:
    def __init__(self,dsn,settings,target):
        if type(settings) is not AccountDirectorySettings or type(target) is not AccountDatabaseTarget:
            raise ValueError('Explicit reviewed server settings and target required')
        self.settings=settings;self.target=target
        self._conninfo=target.conninfo(dsn)

    @property
    def registry_id(self):return self.settings.registry_id

    @contextmanager
    def _connection(self,*,readonly):
        import psycopg
        from psycopg.pq import TransactionStatus
        # No environment-discovered DSN and no target/role fallback.
        with psycopg.connect(self.target.conninfo(self._conninfo)) as conn:
            if readonly:conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            conn.execute('SET LOCAL search_path=pg_catalog')
            conn.execute("SET LOCAL lock_timeout='5s'")
            conn.execute("SET LOCAL statement_timeout='20s'")
            validate_runtime_role(conn,self.target)
            verify_schema(conn,reuse_catalog=readonly)
            yield conn
            if conn.info.transaction_status!=TransactionStatus.INTRANS:
                raise RuntimeError('Account transaction failed; commit blocked')

    def _read(self,method,*args,**kwargs):
        with self._connection(readonly=True) as conn:
            return getattr(DirectoryTransaction(conn,self.settings),method)(*args,**kwargs)

    def session(self,user):return self._read('session',user)
    def snapshot(self,user):return self._read('snapshot',user)
    def authorize(self,user,account_id,*,write=False):return self._read('authorize',user,account_id,write=write)
    def execute(self,user,expected_revision,command,/,**arguments):
        with self._connection(readonly=False) as conn:
            return DirectoryTransaction(conn,self.settings).execute(user,expected_revision,command,**arguments)
