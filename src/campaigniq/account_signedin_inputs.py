"""Bound monthly input service for real server-authenticated LOCAL sessions.

Reuses the existing account-scoped draft/preflight/document-identity behavior.
Only the exact new signed-in gateway is accepted; synthetic gateway guards and
publication entry-point type checks remain unchanged.
"""
from campaigniq.account_import_preview import BoundAccountImportPreviewService
from campaigniq.persistence.signedin_account_data import LocalSignedInInputGateway


class SignedInMonthlyInputs(BoundAccountImportPreviewService):
    def __init__(self, gateway):
        if type(gateway) is not LocalSignedInInputGateway:
            raise ValueError('Real-sign-in local input gateway required')
        self.gateway = gateway

    def reconcile(self, user, account_id, draft_id, revision, *, forex_applicable=True, crypto_applicable=True):
        from campaigniq.account_signedin_reconciliation import reconcile_signedin
        return reconcile_signedin(self, user, account_id, draft_id, revision,
            forex_applicable=forex_applicable, crypto_applicable=crypto_applicable)

    def compare(self, user, year, month, selections, *, include_positions=False):
        from campaigniq.account_signedin_comparison import compare_published
        return compare_published(self, user, year, month, selections, include_positions=include_positions)

    def results(self, user, account_id, draft_id, revision):
        from campaigniq.account_signedin_results import load_published_results
        return load_published_results(self, user, account_id, draft_id, revision)

    def published(self, user, account_id, draft_id, revision):
        from campaigniq.account_signedin_publication import published_signedin
        return published_signedin(self, user, account_id, draft_id, revision)

    def publish(self, user, account_id, draft_id, revision, *, authorize_local_publication=False,
                forex_applicable=True, crypto_applicable=True):
        from campaigniq.account_signedin_publication import publish_signedin
        return publish_signedin(self, user, account_id, draft_id, revision,
            authorize_local_publication=authorize_local_publication,
            forex_applicable=forex_applicable, crypto_applicable=crypto_applicable)

    def save(self, user, account_id, draft_id, revision, role, upload, *, replace=False):
        from campaigniq.account_import_preview import UPLOAD_ROLES
        if role not in UPLOAD_ROLES or type(replace) is not bool or type(revision) is not int:
            raise ValueError('Known role, displayed revision and explicit replacement choice required')
        with self.gateway.transaction(user, account_id, write=True) as session:
            draft = self._lock(session, draft_id, write=True)
            if draft.revision != revision:
                raise ValueError('Draft changed; reload before saving')
            if session.artifacts.list_keys(prefix=f'{draft.year:04d}-{draft.month:02d}-'):
                raise ValueError('Published month documents are preserved; replacement is disabled')
            self._validate_upload(session, role, upload)
            return session.drafts.upload(draft.id, role, upload.name, upload.media_type,
                upload.getvalue(), replace=replace)

    # Inherited methods' first identity argument must be st.user here. The
    # gateway accepts only server-session claims and authorizes every call;
    # passing an Identity object or caller-supplied claims dict is denied.
