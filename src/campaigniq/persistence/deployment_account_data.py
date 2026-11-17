"""Deployment account-scoped drafts and retained legacy reads; no publisher or DDL."""
from contextlib import contextmanager,ExitStack
from campaigniq.navigation_timing import stage
from dataclasses import dataclass
from campaigniq.account_reconciliation_preview import ReadOnlyArtifacts
from campaigniq.broker_account_binding import bind,bindings
from campaigniq.persistence.account_data_session import AccountDataSession,_Drafts,_Artifacts
from campaigniq.persistence.deployment_account_directory import PostgresAccountDirectory,DirectoryTransaction,require_transaction


@dataclass(frozen=True)
class DataAccount:
    id:str
    name:str
    legacy:bool
    archived:bool
    writable:bool
    suffixes:tuple[tuple[str,str],...]


def new_schwab_account(directory,account_id,*,continue_existing=False):
    account=next((a for a in directory.accounts if a.id==account_id),None)
    if account is None or (account.legacy and not continue_existing):raise ValueError('Select a new brokerage account for monthly uploads')
    if account.legacy:
        from campaigniq.account_continuation import expected_months
        expected_months(account.storage_scope)
    if account.broker.strip().casefold() not in {'schwab','charles schwab'}:
        raise ValueError('Monthly uploads support Schwab accounts only')
    return account


def validate_data_role(conn):
    """Ordinary input role can change drafts, never artifacts or retained tables."""
    from campaigniq.account_table_policy_checks import validate_tables
    policy=(
        ('campaigniq_accounts','artifact_storage_dev',{'SELECT'}),
        ('campaigniq_accounts','import_drafts',{'SELECT','INSERT','UPDATE'}),
        ('campaigniq_accounts','import_draft_documents',{'SELECT','INSERT','UPDATE'}),
        ('public','artifact_storage_dev',{'SELECT'}),
        ('public','import_drafts',{'SELECT'}),
        ('public','import_draft_documents',{'SELECT'}))
    validate_tables(conn,policy,check_ownership=True,grants_error='Account input role grants differ')



class _SnapshotArtifacts(_Artifacts):
    """Bounded exact namespace bytes in one proven read-only transaction."""
    MAX_BYTES=32*1024*1024
    MAX_ENTRIES=512
    def __init__(self,session):
        super().__init__(session)
        self._snapshot_scope=None;self._snapshot_rows=None;self._snapshot_declined=False

    def _snapshot(self):
        self._session._check()
        if not self._session._read_snapshot_reusable():return None
        scope=self.namespace
        if scope!=self._snapshot_scope:
            self._snapshot_scope=scope;self._snapshot_rows=None;self._snapshot_declined=False
        if self._snapshot_declined:return None
        if self._snapshot_rows is None:
            with stage('snapshot_artifact_batch_read'), self._session._operation() as conn:
                count,size=conn.execute('SELECT count(*),coalesce(sum(octet_length(artifact_key)+octet_length(content)),0) '
                    'FROM artifact_storage_dev WHERE namespace=%s',(scope,)).fetchone()
                if count>self.MAX_ENTRIES or size>self.MAX_BYTES:
                    self._snapshot_declined=True;return None
                rows=conn.execute('SELECT artifact_key,content FROM artifact_storage_dev WHERE namespace=%s ORDER BY artifact_key',(scope,)).fetchall()
            # The size check and read must describe the same repeatable snapshot.
            if len(rows)!=count or sum(len(k.encode())+len(v.encode()) for k,v in rows)!=size:
                raise ValueError('Artifact snapshot inventory differs; no reuse')
            self._snapshot_rows=dict(rows)
        return self._snapshot_rows

    def exists(self,key):
        from campaigniq.persistence.postgres_artifact_storage import _validate_key
        _validate_key(key);rows=self._snapshot()
        return super().exists(key) if rows is None else key in rows

    def read_text(self,key):
        from campaigniq.persistence.postgres_artifact_storage import _validate_key
        _validate_key(key);rows=self._snapshot()
        if rows is None:return super().read_text(key)
        if key not in rows:raise FileNotFoundError(key)
        return rows[key]

    def list_keys(self,*,prefix='',suffix=''):
        rows=self._snapshot()
        if rows is None:return super().list_keys(prefix=prefix,suffix=suffix)
        return tuple(k for k in sorted(rows) if k.startswith(prefix) and k.endswith(suffix))


class _SnapshotDrafts(_Drafts):
    """Exact originals and bounded batches in one verified read-only session."""
    MAX_BYTES=32*1024*1024
    MAX_ENTRIES=128
    def __init__(self,session):
        super().__init__(session)
        from collections import OrderedDict
        self._documents=OrderedDict();self._document_bytes=0
        self._primed_scope=None;self._primed_roles=frozenset()

    def prime_documents(self,draft_id):
        """Bounded current originals for one draft, never outside this snapshot."""
        self._session._check()
        if not self._session._read_snapshot_reusable():return False
        scope=(self.workspace,draft_id)
        if scope==self._primed_scope:return True
        with stage('snapshot_document_batch_read'),self._session._operation() as conn:
            count,size=conn.execute("""SELECT count(*),coalesce(sum(octet_length(d.filename)+octet_length(d.media_type)+octet_length(d.content)),0)
                FROM import_draft_documents d JOIN import_drafts p ON p.id=d.draft_id
                WHERE p.id=%s AND p.workspace=%s AND p.state='draft' AND d.superseded=false""",(draft_id,self.workspace)).fetchone()
            if count>self.MAX_ENTRIES or size>self.MAX_BYTES:return False
            rows=conn.execute("""SELECT d.role,d.filename,d.media_type,d.content
                FROM import_draft_documents d JOIN import_drafts p ON p.id=d.draft_id
                WHERE p.id=%s AND p.workspace=%s AND p.state='draft' AND d.superseded=false ORDER BY d.role""",(draft_id,self.workspace)).fetchall()
        loaded={}
        for role,name,media,raw in rows:
            if role in loaded:raise ValueError('Document snapshot has duplicate current roles')
            loaded[role]=(name,media,bytes(raw))
        actual=sum(len(name.encode())+len(media.encode())+len(raw) for name,media,raw in loaded.values())
        if len(rows)!=count or actual!=size:raise ValueError('Document snapshot inventory differs; no reuse')
        # Retain one bounded batch in the same LRU used for individual reads.
        self._documents.clear();self._document_bytes=actual
        self._documents.update(((self.workspace,draft_id,role),value) for role,value in loaded.items())
        self._primed_scope=scope;self._primed_roles=frozenset(loaded)
        return True

    def document(self,draft_id,role):
        self._session._check()
        if not self._session._read_snapshot_reusable():return super().document(draft_id,role)
        key=(self.workspace,draft_id,role)
        if (self.workspace,draft_id)==self._primed_scope and role not in self._primed_roles:
            raise FileNotFoundError('document not found')
        if key in self._documents:
            self._documents.move_to_end(key)
            return self._documents[key]
        result=super().document(draft_id,role)
        size=len(result[0].encode())+len(result[1].encode())+len(result[2])
        if size<=self.MAX_BYTES:
            self._documents[key]=result;self._document_bytes+=size
            while self._document_bytes>self.MAX_BYTES or len(self._documents)>self.MAX_ENTRIES:
                _,old=self._documents.popitem(last=False)
                self._document_bytes-=len(old[0].encode())+len(old[1].encode())+len(old[2])
        return result

class DeploymentDataSession(AccountDataSession):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.drafts=_SnapshotDrafts(self)
        self.artifacts=_SnapshotArtifacts(self)

    def _read_snapshot_reusable(self):
        # No cross-transaction cache or writable-session reuse. The gateway closes
        # this session in finally; every cache hit still checks failure/expiry.
        self._check()
        if self._writable:return False
        proof=getattr(self,'_readonly_repeatable_proof',None)
        if proof is None:
            row=self._connection.execute("SELECT current_setting('transaction_read_only'),current_setting('transaction_isolation')").fetchone()
            proof=row==('on','repeatable read')
            self._readonly_repeatable_proof=proof
        return proof

    def _check(self,*,write=False):
        super()._check(write=write)
        require_transaction(self._connection)


def _open_session(conn,settings,user,account_id,*,write=False,legacy=False,continue_existing=False):
    """Internal routing on an already checked connection and current server session."""
    with stage('account_data_session_authorization'):
        directory,actor=DirectoryTransaction(conn,settings)._actor(user)
        access=directory.authorize(actor.identity,account_id,write=write)
        account=next(a for a in directory.accounts if a.id==account_id)
        if legacy:
            if write or not account.legacy:raise ValueError('Retained account access is read-only')
            schema='public';path='pg_catalog,public,pg_temp'
        else:
            new_schwab_account(directory,account_id,continue_existing=continue_existing)
            schema='campaigniq_accounts';path='campaigniq_accounts,pg_catalog,pg_temp'
    with stage('account_data_session_schema'):
        conn.execute('SET LOCAL search_path='+path)
        rows=conn.execute('''SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.oid IN(to_regclass('artifact_storage_dev'),to_regclass('import_drafts'),
                           to_regclass('import_draft_documents')) ORDER BY c.relname''').fetchall()
        if rows!=[(schema,n) for n in ('artifact_storage_dev','import_draft_documents','import_drafts')]:
            raise ValueError('Exact account storage schema missing; no fallback or initialization')
        session=DeploymentDataSession(conn,access,write,settings.registry_id)
        session._physical_scope=access.storage_scope
        # This directory was authorized on this exact financial connection. Reuse
        # it only after proving READ ONLY REPEATABLE READ; never across sessions.
        if not write and session._read_snapshot_reusable():
            session._known_directory_snapshot=directory
        session.artifacts=ReadOnlyArtifacts(session.artifacts)
    if continue_existing and not legacy:
        from campaigniq.account_continuation import attach_continuation
        with stage('snapshot_continuation_setup'):
            attach_continuation(session,account)
    return session,actor.directory_revision


class PostgresAccountDataGateway:
    def __init__(self,store,*,continue_existing=False):
        if type(store) is not PostgresAccountDirectory:raise ValueError('Explicit deployment directory backend required')
        if type(continue_existing) is not bool:raise ValueError("Explicit continuation mode required")
        self._directory=store;self._continue_existing=continue_existing

    @contextmanager
    def _connection(self,*,readonly):
        with ExitStack() as stack:
            with stage('account_data_connection_setup'):
                conn=stack.enter_context(self._directory._connection(readonly=readonly))
                validate_data_role(conn)
            yield conn

    def overview(self,user):
        with self._connection(readonly=True) as conn:
            directory,actor=DirectoryTransaction(conn,self._directory.settings)._actor(user)
            return tuple(DataAccount(a.id,a.name,a.legacy,a.archived,
                (not a.legacy or self._continue_existing) and directory.authorize(actor.identity,a.id).writable,
                tuple(sorted((kind,value.suffix) for kind,value in bindings(directory,a.id).items())))
                for a in directory.accounts if a.id in actor.account_ids)

    def bind(self,user,account_id,kind,number,*,expected_revision,confirm_replace=False):
        with self._connection(readonly=False) as conn:
            tx=DirectoryTransaction(conn,self._directory.settings)
            current,actor=tx._actor(user,lock=True)
            new_schwab_account(current,account_id,continue_existing=self._continue_existing)
            updated=bind(current,actor.identity,account_id,kind,number,
                expected_revision=expected_revision,confirm_replace=confirm_replace)
            if updated==current:return current.revision
            return tx._persist_transition(current,updated)

    @contextmanager
    def _guard(self,user,account_id,*,legacy=False,write=False):
        with self._connection(readonly=False) as conn:
            with stage('account_data_guard_authorization'):
                conn.execute('SELECT registry_id FROM campaigniq_accounts.directories WHERE registry_id=%s FOR SHARE',
                             (self._directory.settings.registry_id,))
                directory,actor=DirectoryTransaction(conn,self._directory.settings)._actor(user)
                directory.authorize(actor.identity,account_id,write=write)
                account=next(a for a in directory.accounts if a.id==account_id)
                if legacy:
                    if not account.legacy or write:raise ValueError('Retained accounts are read-only')
                else:new_schwab_account(directory,account_id,continue_existing=self._continue_existing)
            yield conn

    @contextmanager
    def _transaction(self,user,account_id,*,write=False,legacy=False):
        if type(write) is not bool or type(legacy) is not bool:raise ValueError('Explicit boolean transaction mode required')
        session=None
        with self._guard(user,account_id,write=write,legacy=legacy) as guarded:
            try:
                if write:
                    session,revision=_open_session(guarded,self._directory.settings,user,account_id,write=True,continue_existing=self._continue_existing)
                    yield session,revision
                    session._check()
                else:
                    with self._connection(readonly=True) as conn:
                        session,revision=_open_session(conn,self._directory.settings,user,account_id,legacy=legacy,continue_existing=self._continue_existing)
                        yield session,revision
                        session._check()
            finally:
                if session is not None:session._active=False

    @contextmanager
    def transaction(self,user,account_id,*,write=False):
        with self._transaction(user,account_id,write=write) as (session,_):yield session

    @contextmanager
    def legacy_transaction(self,user,account_id):
        with self._transaction(user,account_id,legacy=True) as (session,revision):
            yield session._connection,session,revision
