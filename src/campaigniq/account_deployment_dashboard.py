"""Deployment report snapshots for existing main loaders; no launcher or UI switch."""
from campaigniq.account_dashboard import ReportSnapshot,DashboardAccount,DashboardChoices
from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
from campaigniq.account_deployment_results import _deployment_results_session
from campaigniq.account_legacy_results import published_months,_results
from campaigniq.account_signedin_publication import _published_session
from campaigniq.account_history_evidence import bind_history,retained_history_rows,ROLE
from campaigniq.broker_account_binding import bindings
from campaigniq.persistence.deployment_account_directory import PostgresAccountDirectory,DirectoryTransaction
from campaigniq.persistence.deployment_account_data import PostgresAccountDataGateway


class DeploymentAccountDashboard:
    def __init__(self,store,*,continue_existing=False):
        if type(store) is not PostgresAccountDirectory:raise ValueError('Explicit deployment directory required')
        self.store=store;self.gateway=PostgresAccountDataGateway(store,continue_existing=continue_existing);self.inputs=DeploymentMonthlyInputs(self.gateway)

    def choices(self,user):
        with self.store._connection(readonly=True) as conn:
            directory,actor=DirectoryTransaction(conn,self.store.settings)._actor(user)
            return DashboardChoices(actor.user_id,actor.administrator,tuple(DashboardAccount(a.id,a.name,a.legacy,a.archived)
                for a in directory.accounts if a.id in actor.account_ids))

    def snapshot(self,user,account_id,*,include_history=False):
        if type(include_history) is not bool:raise ValueError('Explicit history selection required')
        choices=self.choices(user);selected=next((a for a in choices.accounts if a.id==account_id),None)
        if selected is None:raise PermissionError('Selected account is unavailable')
        # Each route reauthorizes in its own guarded read-only financial snapshot.
        if selected.legacy and not self.gateway._continue_existing:
            with self.gateway.legacy_transaction(user,account_id) as (conn,session,_):
                rows=conn.execute('SELECT artifact_key,content FROM public.artifact_storage_dev WHERE namespace=%s ORDER BY artifact_key',(session.account.storage_scope,)).fetchall()
                adapter=[(k,session.artifacts.read_text(k)) for k in session.artifacts.list_keys()]
                if rows!=adapter:raise ValueError('Retained dashboard adapter parity differs')
                for month in published_months(session.artifacts):
                    year,number=map(int,month.split('-'));prefix=month+'-'
                    _results(session.artifacts,account_id,year,number,[(k,v) for k,v in rows if k.startswith(prefix)])
                result=ReportSnapshot(session.account.storage_scope,rows)
                if include_history:
                    directory=DirectoryTransaction(conn,self.store.settings)._load()
                    result.history=bind_history(result,retained_history_rows(conn,session.account.storage_scope,legacy=True),known_bindings=bindings(directory,account_id))
                session._check();return result
        with self.gateway.transaction(user,account_id) as session:
            visible=[]
            for draft in session.drafts.list_drafts():
                self.inputs._lock(session,draft.id)
                if _published_session(self.inputs,session,account_id,draft,draft.revision,receipt_format='campaigniq.deployment_account_publication'):
                    _deployment_results_session(self.inputs,session,draft,account_id,draft.revision)
                    visible.append(f'{draft.year:04d}-{draft.month:02d}-')
            retained=getattr(session,'retained_rows',())
            retained_keys={k for k,v in retained}
            if set(session.artifacts.list_keys(suffix='-finalized.json'))-retained_keys!={p+'finalized.json' for p in visible}:
                raise ValueError('Published generation has no matching saved source draft')
            rows=[(k,session.artifacts.read_text(k)) for k in session.artifacts.list_keys() if k in retained_keys or any(k.startswith(p) for p in visible) or k=='.campaigniq-monthly-publication-v1.json']
            result=ReportSnapshot(session.account.storage_scope,rows)
            if include_history:
                conn=session._connection;directory=DirectoryTransaction(conn,self.store.settings)._load()
                originals=conn.execute('SELECT d.id,d.sha256,d.content FROM campaigniq_accounts.import_draft_documents d '
                    'JOIN campaigniq_accounts.import_drafts p ON p.id=d.draft_id WHERE p.workspace=%s AND d.role=%s ORDER BY d.id',(session.account.storage_scope,ROLE)).fetchall()
                if selected.legacy:originals+=retained_history_rows(conn,session.account.storage_scope,legacy=True)
                result.history=bind_history(result,originals,known_bindings=bindings(directory,account_id))
            session._check();return result
