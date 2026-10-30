"""Deployment-bound monthly inputs and read-only reconciliation. No publication API."""
from hashlib import sha256
from campaigniq.account_import_preview import BoundAccountImportPreviewService,AccountImportPreviewService
from campaigniq.account_signedin_inputs import SignedInMonthlyInputs
from campaigniq.account_reconciliation_preview import _reconcile_session
from campaigniq.persistence.deployment_account_directory import DirectoryTransaction
from campaigniq.persistence.deployment_account_data import PostgresAccountDataGateway


class DeploymentMonthlyInputs(BoundAccountImportPreviewService):
    def __init__(self,gateway):
        if type(gateway) is not PostgresAccountDataGateway:raise ValueError('Deployment account data gateway required')
        self.gateway=gateway

    @staticmethod
    def _lock(session,draft_id,*,write=False):
        # Reads already use one repeatable READ ONLY snapshot. A row SHARE lock
        # would violate SQL read-only mode and is unnecessary for snapshot parity.
        draft=(AccountImportPreviewService._lock(session,draft_id,write=True) if write else session.drafts.get(draft_id))
        with session._operation() as conn:
            rows=conn.execute('''SELECT d.sha256,d.content FROM import_draft_documents d
                JOIN import_drafts p ON p.id=d.draft_id WHERE p.id=%s AND p.workspace=%s ORDER BY d.id''',
                (draft.id,session.drafts.workspace)).fetchall()
        if any(sha256(bytes(content)).hexdigest()!=digest for digest,content in rows):
            raise ValueError('Retained original document checksum differs; no repair attempted')
        return draft

    def _known(self,session):
        from campaigniq.broker_account_binding import bindings
        directory=DirectoryTransaction(session._connection,self.gateway._directory.settings)._load()
        return bindings(directory,session.account.account_id)

    def save(self,user,account_id,draft_id,revision,role,upload,*,replace=False):
        # Reuse the tested bound save path including source revision, published
        # month preservation, replacement confirmation and document identity.
        return SignedInMonthlyInputs.save(self,user,account_id,draft_id,revision,role,upload,replace=replace)

    def reconcile(self,user,account_id,draft_id,revision,*,forex_applicable=True,crypto_applicable=True):
        if type(revision) is not int or any(type(flag) is not bool for flag in (forex_applicable,crypto_applicable)):
            raise ValueError('Displayed revision and explicit market choices required')
        with self.gateway.transaction(user,account_id) as session:
            draft=self._lock(session,draft_id)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before reconciliation')
            return _reconcile_session(self,session,draft,forex_applicable=forex_applicable,crypto_applicable=crypto_applicable)
