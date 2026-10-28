"""Read-only LOCAL access to existing PostgreSQL workspace data after sign-in.

No table creation, financial writes, copying, namespace replacement or automatic
account creation. The audited directory and financial rows share one repeatable
read snapshot. Each public method reauthorizes the server-owned OIDC session.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType

from campaigniq.account_signin import signed_in_accounts, verify_legacy_workspace_bindings
from campaigniq.persistence.account_data_session import AccountDataSession
from campaigniq.persistence.account_directory_codec import checksum
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory


@dataclass(frozen=True)
class LegacyAccountChoice:
    id: str
    name: str
    archived: bool


@dataclass(frozen=True)
class LegacyInventory:
    account_id: str
    storage_scope: str
    directory_revision: int
    artifact_count: int
    active_drafts: int
    abandoned_drafts: int
    document_versions: int
    original_bytes: int
    artifact_sha256: str
    drafts_sha256: str
    documents_sha256: str
    adapter_parity_verified: bool
    readonly: bool = True


class LocalLegacyAccountAccess:
    def __init__(self, store, *, trusted_issuer, authorization):
        if type(store) is not LocalPostgresAccountDirectory:
            raise ValueError('Guarded local directory service required')
        # Server-owned mapping only, copied so callers cannot change routing
        # under an already configured service. No browser workspace parameter.
        self._authorization = MappingProxyType(dict(authorization))
        self._issuer = trusted_issuer
        self._store = store

    @contextmanager
    def _authorized(self, user):
        with self._store._connection(readonly=True) as conn:
            directory = self._store._load(conn)
            verify_legacy_workspace_bindings(directory, self._authorization,
                                             trusted_issuer=self._issuer)
            actor = signed_in_accounts(user, directory, trusted_issuer=self._issuer)
            yield conn, directory, actor

    def choices(self, user):
        with self._authorized(user) as (_, directory, actor):
            return tuple(LegacyAccountChoice(a.id, a.name, a.archived)
                         for a in directory.accounts if a.legacy and a.id in actor.account_ids)

    @contextmanager
    def _session(self, user, account_id):
        session = None
        with self._authorized(user) as (conn, directory, actor):
            access = directory.authorize(actor.identity, account_id)
            account = next(a for a in directory.accounts if a.id == account_id)
            if not account.legacy:
                raise ValueError('This reader supports retained legacy accounts only')
            # Explicit public resolution; never fall back to synthetic tables.
            conn.execute('SET LOCAL search_path = pg_catalog, public')
            rows = conn.execute('''SELECT n.nspname,c.relname FROM pg_class c
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE c.oid IN (to_regclass('artifact_storage_dev'),to_regclass('import_drafts'),
                               to_regclass('import_draft_documents')) ORDER BY c.relname''').fetchall()
            expected = [('public', n) for n in ('artifact_storage_dev', 'import_draft_documents', 'import_drafts')]
            if rows != expected:
                raise ValueError('Existing public workspace tables are missing; no schema was created')
            try:
                session = AccountDataSession(conn, access, False, self._store.registry_id)
                # Retain the exact legacy namespace, unlike synthetic previews.
                session._physical_scope = access.storage_scope
                yield conn, session, actor.directory_revision
                session._check()
            finally:
                if session is not None:
                    session._active = False

    def published_months(self, user, account_id):
        from campaigniq.account_legacy_results import published_months
        with self._session(user, account_id) as (_, session, _):
            return published_months(session.artifacts)

    def published_results(self, user, account_id, year, month):
        from campaigniq.account_legacy_results import load_existing_results
        return load_existing_results(self, user, account_id, year, month)

    def inventory(self, user, account_id):
        """Compare existing adapters with direct source rows in one snapshot.

        Includes abandoned drafts and superseded original document versions in
        fingerprints. No financial values or document content enter the UI.
        """
        with self._session(user, account_id) as (conn, session, revision):
            scope = session.account.storage_scope
            artifacts = conn.execute('''SELECT artifact_key,content FROM public.artifact_storage_dev
                WHERE namespace=%s ORDER BY artifact_key''', (scope,)).fetchall()
            via_adapter = [(key, session.artifacts.read_text(key)) for key in session.artifacts.list_keys()]
            drafts = conn.execute('''SELECT id,year,month,state,revision FROM public.import_drafts
                WHERE workspace=%s ORDER BY year,month,id''', (scope,)).fetchall()
            documents = conn.execute('''SELECT d.id,d.draft_id,d.role,d.filename,d.media_type,
                d.sha256,d.content,d.revision,d.superseded,p.state
                FROM public.import_draft_documents d JOIN public.import_drafts p ON p.id=d.draft_id
                WHERE p.workspace=%s ORDER BY d.id''', (scope,)).fetchall()
            expected_drafts = sorted((str(i), y, m, r) for i, y, m, state, r in drafts if state == 'draft')
            adapter_drafts = sorted((str(d.id), d.year, d.month, d.revision) for d in session.drafts.list_drafts())
            if via_adapter != artifacts or adapter_drafts != expected_drafts:
                raise ValueError('Existing workspace adapter parity differs')
            fingerprints, total_bytes = [], 0
            for identifier, draft_id, role, filename, media, digest, raw, rev, old, state in documents:
                content = bytes(raw)
                if sha256(content).hexdigest() != digest:
                    raise ValueError('Original document checksum differs')
                if state == 'draft' and not old:
                    if session.drafts.document(draft_id, role) != (filename, media, content):
                        raise ValueError('Existing original document adapter differs')
                total_bytes += len(content)
                fingerprints.append((str(identifier), str(draft_id), role, filename, media,
                                     digest, len(content), rev, old, state))
            return LegacyInventory(account_id, scope, revision, len(artifacts),
                sum(d[3] == 'draft' for d in drafts), sum(d[3] == 'abandoned' for d in drafts),
                len(documents), total_bytes, checksum(artifacts),
                checksum([(str(i), y, m, state, r) for i, y, m, state, r in drafts]),
                checksum(fingerprints), True)
