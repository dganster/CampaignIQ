"""Explicit deployment composition for the existing account application views.

Construction opens no connection and does not read environment configuration,
bootstrap, migrate, grant roles, or change an application entry point.
"""
from campaigniq.account_administration import Identity
from campaigniq.account_deployment_dashboard import DeploymentAccountDashboard
from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
from campaigniq.account_deployment_publication import DeploymentAccountPublisher
from campaigniq.account_deployment_results import load_deployment_results,_deployment_results_session
from campaigniq.account_deployment_comparison import compare_deployment_accounts
from campaigniq.account_deployment_preferences import DeploymentAccountPreferences
from campaigniq.account_deployment_legacy import DeploymentLegacyAccountAccess
from campaigniq.account_signedin_publication import _published_session
from campaigniq.persistence.deployment_account_directory import PostgresAccountDirectory,DirectoryTransaction
from campaigniq.persistence.deployment_account_data import PostgresAccountDataGateway
from campaigniq.persistence.signedin_account_data import InputAccounts,InputAccount
from campaigniq.broker_account_binding import bindings


class DeploymentAdministration:
    def __init__(self,store):
        self._store=store;self._issuer=store.settings.trusted_issuer
    @property
    def registry_id(self):return self._store.registry_id
    def snapshot(self,user):return self._store.snapshot(user)
    def execute(self,user,revision,command,/,**arguments):return self._store.execute(user,revision,command,**arguments)
    def add_user(self,user,revision,*,name,subject):
        return self.execute(user,revision,'add_user',name=name,identity=Identity(self._issuer,subject))


class DeploymentInputViewGateway:
    """Presentation summaries only; actual operations use the guarded input gateway."""
    def __init__(self,gateway):self._backend=gateway;self._directory=gateway._directory
    def overview(self,user):
        with self._backend._connection(readonly=True) as conn:
            directory,actor=DirectoryTransaction(conn,self._directory.settings)._actor(user)
            accounts=tuple(InputAccount(a.id,a.name,a.archived,
                directory.authorize(actor.identity,a.id).writable,
                tuple(sorted((kind,value.suffix) for kind,value in bindings(directory,a.id).items())))
                for a in directory.accounts if (not a.legacy or self._backend._continue_existing) and a.id in actor.account_ids)
            return InputAccounts(actor.administrator,directory.revision,accounts,actor.user_id)
    def storage_ready(self,user):
        # The connection validates the versioned schema. No initialize fallback.
        self.overview(user);return True
    def initialize(self,*args,**kwargs):raise PermissionError('Deployment storage requires a reviewed schema migration')
    def bind(self,*args,**kwargs):return self._backend.bind(*args,**kwargs)


class DeploymentMonthlyApplication:
    def __init__(self,inputs,publisher,*,publication_enabled):
        self._inputs=inputs;self._publisher=publisher
        self.publication_enabled=publication_enabled
        self.multi_asset_enabled=getattr(publisher,'multi_asset_enabled',False)
        self.gateway=DeploymentInputViewGateway(inputs.gateway)
    def drafts(self,*args,**kwargs):return self._inputs.drafts(*args,**kwargs)
    def create(self,*args,**kwargs):return self._inputs.create(*args,**kwargs)
    def save(self,*args,**kwargs):return self._inputs.save(*args,**kwargs)
    def save_associated_gain_loss(self,*args,**kwargs):return self._inputs.save_associated_gain_loss(*args,**kwargs)
    def confirm_empty_futures_opening(self,*args,**kwargs):return self._inputs.confirm_empty_futures_opening(*args,**kwargs)
    def futures_opening_status(self,*args,**kwargs):return self._inputs.futures_opening_status(*args,**kwargs)
    def save_futures_statement(self,*args,**kwargs):return self._inputs.save_futures_statement(*args,**kwargs)
    def futures_preview(self,*args,**kwargs):return self._inputs.futures_preview(*args,**kwargs)
    def confirm_empty_futures_closing(self,*args,**kwargs):return self._inputs.confirm_empty_futures_closing(*args,**kwargs)
    def futures_monthly_preview(self,*args,**kwargs):return self._inputs.futures_monthly_preview(*args,**kwargs)
    def futures_generation_preview(self,*args,**kwargs):return self._inputs.futures_generation_preview(*args,**kwargs)
    def save_forex_daily_sources(self,*args,**kwargs):return self._inputs.save_forex_daily_sources(*args,**kwargs)
    def forex_daily_source_preview(self,*args,**kwargs):return self._inputs.forex_daily_source_preview(*args,**kwargs)
    def forex_daily_inventory_preview(self,*args,**kwargs):return self._inputs.forex_daily_inventory_preview(*args,**kwargs)
    def forex_inventory_preview(self,*args,**kwargs):return self._inputs.forex_inventory_preview(*args,**kwargs)
    def review_forex_cash_sources(self,*args,**kwargs):return self._inputs.review_forex_cash_sources(*args,**kwargs)
    def save_forex_cash_sources(self,*args,**kwargs):return self._inputs.save_forex_cash_sources(*args,**kwargs)
    def forex_cash_source_preview(self,*args,**kwargs):return self._inputs.forex_cash_source_preview(*args,**kwargs)
    def review_crypto_statements(self,*args,**kwargs):return self._inputs.review_crypto_statements(*args,**kwargs)
    def save_crypto_statement_sources(self,*args,**kwargs):return self._inputs.save_crypto_statement_sources(*args,**kwargs)
    def crypto_statement_preview(self,*args,**kwargs):return self._inputs.crypto_statement_preview(*args,**kwargs)
    def crypto_generation_preview(self,*args,**kwargs):return self._inputs.crypto_generation_preview(*args,**kwargs)
    def preview(self,*args,**kwargs):return self._inputs.preview(*args,**kwargs)
    def reconcile(self,*args,**kwargs):return self._inputs.reconcile(*args,**kwargs)
    def results(self,*args,**kwargs):return load_deployment_results(self._inputs,*args,**kwargs)
    def compare(self,*args,**kwargs):return compare_deployment_accounts(self._inputs,*args,**kwargs)
    def published(self,user,account_id,draft_id,revision):
        if type(revision) is not int:raise ValueError('Displayed source revision required')
        with self._inputs.gateway.transaction(user,account_id) as session:
            draft=self._inputs._lock(session,draft_id)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before checking publication')
            visible=_published_session(self._inputs,session,account_id,draft,revision,receipt_format='campaigniq.deployment_account_publication')
            if visible:_deployment_results_session(self._inputs,session,draft,account_id,revision)
            session._check();return visible
    def publish(self,user,account_id,draft_id,revision,*,confirm_publication=False,forex_applicable=True,crypto_applicable=True):
        if self.publication_enabled is not True:raise PermissionError('Publication has not been enabled for this application')
        return self._publisher.publish(user,account_id,draft_id,revision,confirm_publication=confirm_publication,
            forex_applicable=forex_applicable,crypto_applicable=crypto_applicable)


class DeploymentLegacyView:
    def __init__(self,store,gateway):self._store=store;self._backend=DeploymentLegacyAccountAccess(gateway)
    def inventory(self,*args,**kwargs):return self._backend.inventory(*args,**kwargs)
    def published_months(self,*args,**kwargs):return self._backend.published_months(*args,**kwargs)
    def published_results(self,*args,**kwargs):return self._backend.published_results(*args,**kwargs)


class DeploymentApplication:
    def __init__(self,input_store,publication_store,preference_store,*,publication_enabled=False,continue_existing=False,multi_asset_enabled=False):
        if type(multi_asset_enabled) is not bool:raise ValueError('Explicit multi-asset availability required')
        stores=(input_store,publication_store,preference_store)
        if any(type(store) is not PostgresAccountDirectory for store in stores):raise ValueError('Three explicit deployment directory services required')
        if type(publication_enabled) is not bool:raise ValueError('Explicit publication availability required')
        endpoint=lambda store:(store.target.host,store.target.port,store.target.database,store.target.transport)
        if (any(store.settings!=input_store.settings or endpoint(store)!=endpoint(input_store) for store in stores)
            or len({store.target.user for store in stores})!=3):
            raise ValueError('Same reviewed target and trusted mapping with three distinct roles required')
        self.store=input_store;self.publication_enabled=publication_enabled
        self.continue_existing=continue_existing
        self._dashboard=DeploymentAccountDashboard(input_store,continue_existing=continue_existing)
        self.administration=DeploymentAdministration(input_store)
        inputs=self._dashboard.inputs
        self.inputs=DeploymentMonthlyApplication(inputs,DeploymentAccountPublisher(inputs,publication_store,multi_asset_enabled=multi_asset_enabled),publication_enabled=publication_enabled)
        self.preferences=DeploymentAccountPreferences(preference_store)
        self.legacy=DeploymentLegacyView(input_store,inputs.gateway)
    def choices(self,*args,**kwargs):return self._dashboard.choices(*args,**kwargs)
    def snapshot(self,*args,**kwargs):return self._dashboard.snapshot(*args,**kwargs)


def render_application(st,application):
    """Explicit injection only; no automatic runtime selection or configuration."""
    if type(application) is not DeploymentApplication:raise ValueError('Explicit deployment application required')
    from campaigniq.ui.account_dashboard_view import render
    render(st,application,administration=application.administration,deployment=True)
