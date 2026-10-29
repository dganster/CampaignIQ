"""Authorized LOCAL dashboard sources; no runtime construction or migrations."""
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from campaigniq.account_administration import Identity
from campaigniq.account_legacy_access import LocalLegacyAccountAccess
from campaigniq.account_signin import signed_in_accounts, verify_legacy_workspace_bindings
from campaigniq.account_signedin_inputs import SignedInMonthlyInputs
from campaigniq.account_signedin_results import _results_session
from campaigniq.persistence.account_data_session import AccountDataSession
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
from campaigniq.persistence.signedin_account_data import LocalSignedInInputGateway, _new_schwab_account
from campaigniq.dashboard_sources import published_artifact_keys


class ReportSnapshot:
    """Detached immutable account bytes. Never a reusable database capability."""
    def __init__(self, namespace, rows):
        self.namespace = namespace
        self._rows = MappingProxyType(dict(rows))
    def exists(self, key): return key in self._rows
    def read_text(self, key): return self._rows[key]
    def list_keys(self, *, prefix='', suffix=''):
        return tuple(sorted(k for k in self._rows if k.startswith(prefix) and k.endswith(suffix)))
    def write_text(self, *a, **kw): raise PermissionError('Dashboard report snapshot is read-only')
    def delete(self, *a, **kw): raise PermissionError('Dashboard report snapshot is read-only')
    def initialize(self): raise PermissionError('Dashboard report snapshot cannot initialize storage')


@dataclass(frozen=True)
class DashboardAccount:
    id: str
    name: str
    legacy: bool
    archived: bool


@dataclass(frozen=True)
class DashboardChoices:
    user_id: str
    administrator: bool
    accounts: tuple[DashboardAccount, ...]


class LocalAccountDashboard:
    def __init__(self, store, *, trusted_issuer, authorization):
        if type(store) is not LocalPostgresAccountDirectory:
            raise ValueError('Guarded local directory required')
        Identity(trusted_issuer, 'configuration-validation')
        self.store = store
        self.issuer = trusted_issuer
        self.authorization = MappingProxyType(dict(authorization))
        from campaigniq.account_preferences import LocalAccountPreferences
        self.preferences = LocalAccountPreferences(store,trusted_issuer=trusted_issuer,authorization=authorization)
        self.legacy = LocalLegacyAccountAccess(store, trusted_issuer=trusted_issuer, authorization=authorization)
        self.inputs = SignedInMonthlyInputs(LocalSignedInInputGateway(store,
            trusted_issuer=trusted_issuer, authorization=authorization))

    def choices(self, user):
        with self.store._connection(readonly=True) as conn:
            directory = self.store._load(conn)
            verify_legacy_workspace_bindings(directory, self.authorization, trusted_issuer=self.issuer)
            actor = signed_in_accounts(user, directory, trusted_issuer=self.issuer)
            return DashboardChoices(actor.user_id, actor.administrator, tuple(
                DashboardAccount(a.id, a.name, a.legacy, a.archived)
                for a in directory.accounts if a.id in actor.account_ids))

    def snapshot(self, user, account_id):
        # Authorize again: a selected UI account or prior overview is no capability.
        with self.legacy._authorized(user) as (_, directory, actor):
            directory.authorize(actor.identity, account_id)
            account = next(a for a in directory.accounts if a.id == account_id)
        if account.legacy:
            with self.legacy._session(user, account_id) as (conn, session, _):
                # Compare adapters against exact namespace rows in this same snapshot.
                rows = conn.execute('SELECT artifact_key,content FROM public.artifact_storage_dev '
                    'WHERE namespace=%s ORDER BY artifact_key', (session.account.storage_scope,)).fetchall()
                adapter = [(key, session.artifacts.read_text(key)) for key in session.artifacts.list_keys()]
                if rows != adapter: raise ValueError('Legacy report source parity differs')
                return ReportSnapshot(session.account.storage_scope, rows)
        gateway = self.inputs.gateway
        # Like the results service: hold directory SHARE lock, financial reads
        # in a separate READ ONLY repeatable snapshot, and expire all handles.
        with gateway.transaction(user, account_id) as guard:
            session = None
            with self.store._connection(readonly=True) as conn:
                directory, actor = gateway._current(conn, user)
                access = directory.authorize(actor.identity, account_id)
                _new_schwab_account(directory, account_id)
                if not gateway._ready(conn): raise ValueError('Local input storage is not initialized')
                conn.execute('SET LOCAL search_path=campaigniq_signedin_accounts_dev,pg_catalog')
                try:
                    session = AccountDataSession(conn, access, False, self.store.registry_id)
                    session._physical_scope = 'signedin-' + UUID(self.store.registry_id).hex + '-' + UUID(account_id).hex
                    # Explicit markers AND source receipts are required for new accounts.
                    from campaigniq.account_signedin_publication import _published_session
                    visible = []
                    for draft in session.drafts.list_drafts():
                        if _published_session(self.inputs, session, account_id, draft, draft.revision):
                            _results_session(self.inputs, session, draft, account_id, draft.revision)
                            visible.append(f'{draft.year:04d}-{draft.month:02d}-')
                    marker_keys = set(session.artifacts.list_keys(suffix='-finalized.json'))
                    if marker_keys != {prefix+'finalized.json' for prefix in visible}:
                        raise ValueError('Published generation has no matching saved source draft')
                    rows = [(k, session.artifacts.read_text(k)) for k in session.artifacts.list_keys()
                            if any(k.startswith(prefix) for prefix in visible)]
                    result = ReportSnapshot(session.artifacts.namespace, rows)
                    session._check()
                    return result
                finally:
                    if session is not None: session._active = False
