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

    def save_associated_gain_loss(self,user,account_id,draft_id,revision,upload,*,confirm_same_account=False,replace=False):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        import json
        from campaigniq.import_draft_bridge import restore_uploads
        from campaigniq.monthly_upload_staging import stage_monthly_uploads
        from campaigniq.account_gain_loss_association import ROLE,RECEIPT_ROLE,make_receipt
        from campaigniq.persistence.import_drafts import MAX_DOCUMENT_BYTES
        if confirm_same_account is not True or type(replace) is not bool or type(revision) is not int:
            raise ValueError('Explicit same-account confirmation, revision and replacement choice required')
        content=upload.getvalue()
        if (not content or len(content)>MAX_DOCUMENT_BYTES or upload.media_type not in ('application/pdf','text/csv','text/plain','application/vnd.ms-excel')):
            raise ValueError('Supported nonempty report at most 20 MiB required')
        with self.gateway.transaction(user,account_id,write=True) as session:
            draft=self._lock(session,draft_id,write=True)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before saving')
            if session.artifacts.list_keys(prefix=f'{draft.year:04d}-{draft.month:02d}-'):
                raise ValueError('Published month documents are preserved; replacement is disabled')
            uploads=restore_uploads(session.drafts,draft.id)
            if 'schwab_brokerage_statement' not in uploads:raise ValueError('Save the Brokerage Statement for this month first')
            uploads[ROLE]=upload
            directory,actor=DirectoryTransaction(session._connection,self.gateway._directory.settings)._actor(user)
            directory.authorize(actor.identity,account_id,write=True)
            with TemporaryDirectory(prefix='campaigniq-report-confirmation-') as temporary:
                supplied=stage_monthly_uploads(upload_dir=Path(temporary),uploads=uploads)
                receipt=make_receipt(session,draft,actor.user_id,self._known(session),self._path(ROLE,supplied),
                    self._path('schwab_brokerage_statement',supplied),content,uploads['schwab_brokerage_statement'].getvalue())
            session.drafts.upload(draft.id,ROLE,upload.name,upload.media_type,content,replace=replace)
            # Both inserts and revision changes share the caller's SQL transaction.
            return session.drafts.upload(draft.id,RECEIPT_ROLE,'gain-loss-account-confirmation.json','text/plain',
                (json.dumps(receipt,sort_keys=True,indent=2)+'\n').encode(),replace=True)

    def _identity_validations(self,session,uploads,supplied,*,draft=None):
        from campaigniq.account_import_preview import InputStatus
        from campaigniq.account_gain_loss_association import ROLE,load_receipt,validate_receipt
        checks=super()._identity_validations(session,uploads,supplied,draft=draft)
        if draft is None or ROLE not in uploads:return checks
        try:
            receipt=load_receipt(session,draft)
            if receipt is None:return checks
            validate_receipt(self,session,draft,uploads,supplied)
            replacement=InputStatus('account:'+ROLE,True,'Account association confirmed by user against the verified statement; not a broker-supplied full identifier.')
        except (ValueError,KeyError,TypeError) as error:
            replacement=InputStatus('account:'+ROLE,False,str(error))
        return tuple(replacement if item.role=='account:'+ROLE else item for item in checks)
