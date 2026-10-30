"""Explicit offline reviewed bootstrap; never invoked by login or page rendering."""
from dataclasses import dataclass
from campaigniq.account_administration import Directory,Identity,LegacyOwner,propose_legacy_directory
from campaigniq.account_backend_config import AccountDirectorySettings
from campaigniq.account_schema_migrations import verify_schema
from campaigniq.account_signin import verify_legacy_workspace_bindings
from campaigniq.persistence.account_directory_codec import checksum,encode
from campaigniq.persistence.deployment_account_directory import DirectoryTransaction,require_transaction


@dataclass(frozen=True)
class DirectoryBootstrapPlan:
    settings_sha256: str
    directory: Directory
    legacy_rows_sha256: str

    @property
    def sha256(self):
        return checksum(dict(format='campaigniq.directory_bootstrap',version=1,
            settings_sha256=self.settings_sha256,directory=encode(self.directory),
            legacy_rows_sha256=self.legacy_rows_sha256))


def legacy_fingerprint(conn,settings,*,lock=False):
    """Bind complete existing workspace rows, including all document versions.

    Only the exact configured namespaces are retained as owner bindings; shared
    namespaces remain untouched. No identity is inferred from the stored rows.
    """
    require_transaction(conn)
    from psycopg import sql
    conn.execute("SET LOCAL search_path=pg_catalog")
    if lock:
        for name in ('artifact_storage_dev','import_drafts','import_draft_documents'):
            conn.execute(sql.SQL('LOCK TABLE public.{} IN SHARE MODE').format(sql.Identifier(name)))
    conn.execute("SET LOCAL timezone='UTC'")
    observed={row[0] for row in conn.execute('''SELECT namespace FROM public.artifact_storage_dev
        WHERE namespace LIKE 'workspace-%' UNION SELECT workspace FROM public.import_drafts
        WHERE workspace LIKE 'workspace-%' ''').fetchall()}
    expected=set(settings.authorization.values())
    if observed!=expected:raise ValueError('Complete restored legacy namespace mapping differs')
    # Hash complete public storage, including shared records and abandoned or
    # superseded originals, so none can change between review and bootstrap.
    rows=[]
    for name in ('artifact_storage_dev','import_drafts','import_draft_documents'):
        values=conn.execute(sql.SQL('SELECT md5(row_to_json(t)::text),count(*) FROM public.{} t GROUP BY 1 ORDER BY 1')
                            .format(sql.Identifier(name))).fetchall()
        rows.append((name,values))
    return checksum(rows)


def review_bootstrap(conn,settings,owners,administrator):
    require_transaction(conn)
    if type(settings) is not AccountDirectorySettings or type(administrator) is not Identity:
        raise ValueError('Explicit trusted settings and administrator required')
    if type(owners) is not tuple or any(type(o) is not LegacyOwner for o in owners):
        raise ValueError('Explicit immutable legacy owners required')
    if any(o.identity.issuer!=settings.trusted_issuer for o in owners):raise ValueError('Owner issuer differs')
    if administrator.issuer!=settings.trusted_issuer:raise ValueError('Administrator issuer differs')
    ordered=tuple(sorted(owners,key=lambda o:o.identity.subject))
    initial=propose_legacy_directory(ordered,administrator)
    verify_legacy_workspace_bindings(initial,settings.authorization,trusted_issuer=settings.trusted_issuer)
    if any(not a.legacy for a in initial.accounts) or initial.events or initial.grants:
        raise ValueError('Only pristine explicit legacy owner bootstrap is supported')
    return DirectoryBootstrapPlan(settings.sha256,initial,legacy_fingerprint(conn,settings))


def bootstrap_directory(conn,settings,plan,*,reviewed_sha256):
    """Atomic metadata insert inside caller transaction. No commit, overwrite or DDL."""
    require_transaction(conn)
    if type(settings) is not AccountDirectorySettings:raise ValueError('Trusted settings required')
    if type(plan) is not DirectoryBootstrapPlan or plan.settings_sha256!=settings.sha256 or reviewed_sha256!=plan.sha256:
        raise ValueError('Reviewed bootstrap plan or server settings differs')
    # Recompute the pristine proposal from its owners; reject forged revisions,
    # grants, synthetic new accounts, disabled owners or alternate stable IDs.
    owners=tuple(LegacyOwner(u.identity,u.workspace_id,u.name,
        next(a.name for a in plan.directory.accounts if a.owner_id==u.id),
        next(a.broker for a in plan.directory.accounts if a.owner_id==u.id)) for u in plan.directory.users)
    admin=tuple(u.identity for u in plan.directory.users if u.admin)
    if len(admin)!=1 or review_bootstrap(conn,settings,owners,admin[0])!=plan:
        raise ValueError('Pristine complete bootstrap proposal required')
    verify_schema(conn)
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('campaigniq_accounts:bootstrap',0))")
    if legacy_fingerprint(conn,settings,lock=True)!=plan.legacy_rows_sha256:
        raise ValueError('Legacy source rows changed since bootstrap review')
    registries=conn.execute('SELECT registry_id::text FROM campaigniq_accounts.directories ORDER BY registry_id').fetchall()
    if registries and registries!=[(settings.registry_id,)]:
        raise ValueError('Existing deployment registry differs; no replacement')
    from psycopg.types.json import Jsonb
    document=encode(plan.directory)
    result=conn.execute('''INSERT INTO campaigniq_accounts.directories
        (registry_id,revision,document,document_sha256) VALUES(%s,0,%s,%s)
        ON CONFLICT(registry_id) DO NOTHING''',(settings.registry_id,Jsonb(document),checksum(document)))
    if DirectoryTransaction(conn,settings)._load()!=plan.directory:
        raise ValueError('Existing directory differs; bootstrap cannot overwrite it')
    return result.rowcount==1
