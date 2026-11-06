"""Deployment account-scoped drafts and retained legacy reads; no publisher or DDL."""
from contextlib import contextmanager
from dataclasses import dataclass
from campaigniq.account_reconciliation_preview import ReadOnlyArtifacts
from campaigniq.broker_account_binding import bind,bindings
from campaigniq.persistence.account_data_session import AccountDataSession,_Drafts
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



class _SnapshotDrafts(_Drafts):
    """Successful exact document reads, scoped to one verified read-only session."""
    MAX_BYTES=32*1024*1024
    MAX_ENTRIES=128
    def __init__(self,session):
        super().__init__(session)
        from collections import OrderedDict
        self._documents=OrderedDict();self._document_bytes=0

    def document(self,draft_id,role):
        self._session._check()
        if not self._session._read_snapshot_reusable():return super().document(draft_id,role)
        key=(self.workspace,draft_id,role)
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
    directory,actor=DirectoryTransaction(conn,settings)._actor(user)
    access=directory.authorize(actor.identity,account_id,write=write)
    account=next(a for a in directory.accounts if a.id==account_id)
    if legacy:
        if write or not account.legacy:raise ValueError('Retained account access is read-only')
        schema='public';path='pg_catalog,public,pg_temp'
    else:
        new_schwab_account(directory,account_id,continue_existing=continue_existing)
        schema='campaigniq_accounts';path='campaigniq_accounts,pg_catalog,pg_temp'
    conn.execute('SET LOCAL search_path='+path)
    rows=conn.execute('''SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.oid IN(to_regclass('artifact_storage_dev'),to_regclass('import_drafts'),
                       to_regclass('import_draft_documents')) ORDER BY c.relname''').fetchall()
    if rows!=[(schema,n) for n in ('artifact_storage_dev','import_draft_documents','import_drafts')]:
        raise ValueError('Exact account storage schema missing; no fallback or initialization')
    session=DeploymentDataSession(conn,access,write,settings.registry_id)
    session._physical_scope=access.storage_scope
    session.artifacts=ReadOnlyArtifacts(session.artifacts)
    if continue_existing and not legacy:
        from campaigniq.account_continuation import attach_continuation
        attach_continuation(session,account)
    return session,actor.directory_revision


class PostgresAccountDataGateway:
    def __init__(self,store,*,continue_existing=False):
        if type(store) is not PostgresAccountDirectory:raise ValueError('Explicit deployment directory backend required')
        if type(continue_existing) is not bool:raise ValueError("Explicit continuation mode required")
        self._directory=store;self._continue_existing=continue_existing

    @contextmanager
    def _connection(self,*,readonly):
        with self._directory._connection(readonly=readonly) as conn:
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
