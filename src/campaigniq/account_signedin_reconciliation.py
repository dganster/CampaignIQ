"""Read-only closing inventory replay for real-sign-in LOCAL new accounts."""
from campaigniq.account_import_preview import AccountImportPreviewService
from campaigniq.account_reconciliation_preview import ReadOnlyArtifacts, _reconcile_session
from campaigniq.account_signedin_inputs import SignedInMonthlyInputs
from campaigniq.persistence.account_data_session import AccountDataSession


def reconcile_signedin(service, user, account_id, draft_id, revision, *,
                       forex_applicable=True, crypto_applicable=True):
    if type(service) is not SignedInMonthlyInputs:
        raise ValueError('Signed-in monthly input service required')
    if type(revision) is not int or any(type(v) is not bool for v in (forex_applicable, crypto_applicable)):
        raise ValueError('Displayed draft revision and boolean market choices required')
    gateway = service.gateway
    # The outer connection holds directory and draft SHARE locks. Revocation,
    # binding changes and document replacement wait until this preview ends.
    with gateway.transaction(user, account_id) as guard:
        locked = AccountImportPreviewService._lock(guard, draft_id)
        if locked.revision != revision:
            raise ValueError('Draft changed; reload before reconciliation')
        # A separate READ ONLY snapshot is required: PostgreSQL prohibits the
        # SHARE locks above inside a READ ONLY transaction. No data writes are
        # possible in this second connection, even through an injected SQL call.
        session = None
        with gateway._directory._connection(readonly=True) as conn:
            directory, actor = gateway._current(conn, user)
            access = directory.authorize(actor.identity, account_id)
            if not gateway._ready(conn):
                raise ValueError('Dedicated local input tables are missing')
            conn.execute('SET LOCAL search_path=campaigniq_signedin_accounts_dev,pg_catalog')
            try:
                session = AccountDataSession(conn, access, False, gateway._directory.registry_id)
                session._physical_scope = guard.drafts.workspace
                session.artifacts = ReadOnlyArtifacts(session.artifacts)
                draft = session.drafts.get(draft_id)
                if draft.revision != revision:
                    raise ValueError('Draft changed; reload before reconciliation')
                result = _reconcile_session(service, session, draft,
                    forex_applicable=forex_applicable, crypto_applicable=crypto_applicable)
                session._check()
                return result
            finally:
                if session is not None:
                    session._active = False
