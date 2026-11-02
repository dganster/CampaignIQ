"""Unactivated deployment publication service with a separate server-owned role.

No environment configuration, UI entry point, bootstrap or production launcher.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
from campaigniq.account_local_publication import _publish_in_session
from campaigniq.account_publication_roles import validate_publication_role
from campaigniq.persistence.deployment_account_directory import PostgresAccountDirectory,DirectoryTransaction
from campaigniq.persistence.deployment_account_data import DeploymentDataSession,new_schwab_account
from campaigniq.persistence.monthly_publication import is_month_published_in_storage


@dataclass(frozen=True)
class DeploymentPublication:
    account_id:str
    year:int
    month:int
    draft_id:str
    revision:int
    artifact_keys:tuple[str,...]
    receipt_sha256:str



class AppendOnlyArtifacts:
    def __init__(self,session):self._session=session;self._reader=session.artifacts
    def __getattr__(self,name):return getattr(self._reader,name)
    def write_text(self,key,content):
        self._session._check(write=True)
        if hasattr(self._session,'retained_rows'):
            from campaigniq.account_continuation import future_key,validate_protocol
            future_key(key)
            if key=='.campaigniq-monthly-publication-v1.json':validate_protocol(content)
            if self._reader.exists(key):raise PermissionError('Existing continuation or retained artifacts cannot be replaced')
        with self._session._operation() as conn:
            conn.execute('INSERT INTO campaigniq_accounts.artifact_storage_dev(namespace,artifact_key,content) VALUES(%s,%s,%s)',
                (self.namespace,key,content))
    def delete(self,key):
        # Existing engine removes optional/visibility keys before first write.
        # On an append-only generation those keys must be absent already.
        if self.exists(key):raise PermissionError('Existing publication artifacts cannot be deleted')


class DeploymentAccountPublisher:
    def __init__(self,inputs,writer):
        if type(inputs) is not DeploymentMonthlyInputs or type(writer) is not PostgresAccountDirectory:
            raise ValueError('Explicit deployment input service and publication directory required')
        reader=inputs.gateway._directory
        if (reader.settings!=writer.settings or reader.target.host!=writer.target.host
            or reader.target.database!=writer.target.database or reader.target.port!=writer.target.port
            or reader.target.user==writer.target.user):
            raise ValueError('Separate publication role on the exact same reviewed account backend required')
        self.inputs=inputs;self.writer=writer

    @contextmanager
    def _transaction(self,user,account_id):
        session=None
        with self.writer._connection(readonly=False) as conn:
            validate_publication_role(conn)
            try:
                conn.execute('SELECT registry_id FROM campaigniq_accounts.directories WHERE registry_id=%s FOR SHARE',(self.writer.settings.registry_id,))
                directory,actor=DirectoryTransaction(conn,self.writer.settings)._actor(user)
                access=directory.authorize(actor.identity,account_id,write=True)
                account=new_schwab_account(directory,account_id,continue_existing=self.inputs.gateway._continue_existing)
                conn.execute('SET LOCAL search_path=campaigniq_accounts,pg_catalog,pg_temp')
                rows=conn.execute("""SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE c.oid IN(to_regclass('artifact_storage_dev'),to_regclass('import_drafts'),to_regclass('import_draft_documents')) ORDER BY c.relname""").fetchall()
                if rows!=[('campaigniq_accounts',n) for n in ('artifact_storage_dev','import_draft_documents','import_drafts')]:
                    raise ValueError('Exact publication storage schema missing')
                session=DeploymentDataSession(conn,access,True,self.writer.registry_id)
                session._physical_scope=access.storage_scope
                conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(access.storage_scope,))
                if self.inputs.gateway._continue_existing:
                    from campaigniq.account_continuation import attach_continuation
                    attach_continuation(session,account)
                session.artifacts=AppendOnlyArtifacts(session)
                yield session,actor.user_id
                session._check()
            finally:
                if session is not None:session._active=False

    def publish(self,user,account_id,draft_id,revision,*,confirm_publication=False,forex_applicable=True,crypto_applicable=True):
        if confirm_publication is not True:raise PermissionError('Explicit publication confirmation required')
        if type(revision) is not int or any(type(flag) is not bool for flag in (forex_applicable,crypto_applicable)):
            raise ValueError('Displayed source revision and explicit market choices required')
        with self._transaction(user,account_id) as (session,actor):
            from campaigniq.futures_monthly_preview import has_futures
            draft=self.inputs._lock(session,draft_id,write=True)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before publication')
            if has_futures(session,draft):
                raise PermissionError('Futures monthly publication is disabled pending complete generation/report integration; use the read-only monthly preview. No rows changed.')
            result=_publish_in_session(self.inputs,session,actor,account_id,draft_id,revision,
                forex_applicable=forex_applicable,crypto_applicable=crypto_applicable,
                receipt_format='campaigniq.deployment_account_publication')
            return DeploymentPublication(result.account_id,result.year,result.month,result.draft_id,result.revision,result.artifact_keys,result.receipt_sha256)
