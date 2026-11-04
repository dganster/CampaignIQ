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

    def confirm_empty_futures_opening(self,user,account_id,draft_id,revision,*,confirm_empty_opening=False):
        """Owner assertion only. It grants no access and never publishes artifacts."""
        import json
        from campaigniq.account_futures_opening import ROLE,source_context,make_receipt,load_receipt
        if confirm_empty_opening is not True or type(revision) is not int or revision < 0:
            raise ValueError('Explicit empty-opening confirmation and displayed revision required')
        with self.gateway.transaction(user,account_id,write=True) as session:
            draft=self._lock(session,draft_id,write=True)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before confirming')
            if session.artifacts.list_keys(prefix=f'{draft.year:04d}-{draft.month:02d}-'):
                raise ValueError('Published month opening confirmations are preserved')
            directory,actor=DirectoryTransaction(session._connection,self.gateway._directory.settings)._actor(user)
            directory.authorize(actor.identity,account_id,write=True)
            account=next(a for a in directory.accounts if a.id==account_id)
            if account.owner_id!=actor.user_id:raise PermissionError('Only the account owner can confirm opening positions')
            prior=load_receipt(session,draft) # Corrupt prior assertions cannot be silently replaced.
            context=source_context(self,session,draft)
            if prior is not None and all(prior[key]==value for key,value in context.items()):
                return draft.revision
            receipt=make_receipt(session,draft,actor.user_id,context)
            revision=session.drafts.upload(draft.id,ROLE,'futures-empty-opening-confirmation.json','text/plain',
                (json.dumps(receipt,sort_keys=True,indent=2)+'\n').encode(),replace=prior is not None)
            # Verify inside the same transaction so an injected failure cannot leave a receipt.
            from campaigniq.account_futures_opening import validate_receipt
            if validate_receipt(self,session,session.drafts.get(draft.id))!=receipt:
                raise ValueError('Futures opening confirmation post-write verification failed')
            return revision

    def futures_opening_status(self,user,account_id,draft_id,revision):
        from campaigniq.account_futures_opening import load_receipt,validate_receipt
        if type(revision) is not int or revision < 0:raise ValueError('Displayed revision required')
        with self.gateway.transaction(user,account_id) as session:
            draft=self._lock(session,draft_id)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before reading confirmation')
            receipt=load_receipt(session,draft)
            if receipt is None:return dict(confirmed=False,evidence_type='missing',broker_verified=False,publication_authorized=False)
            receipt=validate_receipt(self,session,draft)
            return dict(confirmed=True,evidence_type='user_confirmation',broker_verified=False,
                publication_authorized=False,opening_as_of=receipt['opening_as_of'])

    def save_futures_statement(self,user,account_id,draft_id,revision,upload,*,confirm_same_account=False,replace=False):
        from campaigniq import account_futures_preview as futures
        from campaigniq.persistence.import_drafts import MAX_DOCUMENT_BYTES
        import json
        if confirm_same_account is not True or type(replace) is not bool or type(revision) is not int or revision<0:
            raise ValueError('Explicit futures statement association, displayed revision and replacement choice required')
        content=upload.getvalue()
        if upload.media_type!='application/pdf' or not content.startswith(b'%PDF-') or len(content)>MAX_DOCUMENT_BYTES:
            raise ValueError('A futures monthly statement PDF at most 20 MiB is required')
        with self.gateway.transaction(user,account_id,write=True) as session:
            draft=self._lock(session,draft_id,write=True)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before saving futures statement')
            if session.artifacts.list_keys(prefix=f'{draft.year:04d}-{draft.month:02d}-'):
                raise ValueError('Published month futures originals are preserved')
            directory,actor=DirectoryTransaction(session._connection,self.gateway._directory.settings)._actor(user)
            directory.authorize(actor.identity,account_id,write=True)
            account=next(a for a in directory.accounts if a.id==account_id)
            if account.owner_id!=actor.user_id:raise PermissionError('Only the account owner can associate a futures statement')
            prior=futures.load_receipt(session,draft)
            original=futures.existing_pdf(session,draft,prior)
            if original is not None and original!=content and not replace:
                raise ValueError('Confirm replacement of the futures statement; the prior original is preserved')
            context,_=futures.evaluate(self,session,draft,content)
            if prior is not None and prior['gmi_account_digest']!=context['gmi_account_digest']:
                raise ValueError('Futures statement GMI account changed; separate account review required')
            if prior is not None and all(prior[key]==value for key,value in context.items()):return draft.revision
            receipt=futures.make_receipt(session,draft,actor.user_id,context)
            if original!=content:
                session.drafts.upload(draft.id,futures.PDF_ROLE,upload.name,'application/pdf',content,replace=original is not None)
            result=session.drafts.upload(draft.id,futures.RECEIPT_ROLE,'futures-statement-association.json','text/plain',
                (json.dumps(receipt,sort_keys=True,indent=2)+'\n').encode(),replace=prior is not None)
            if futures.load_receipt(session,session.drafts.get(draft.id))!=receipt:
                raise ValueError('Futures statement association post-write verification failed')
            from campaigniq import account_futures_opening as opening
            for role,expected in ((futures.PDF_ROLE,context['statement_sha256']),
                    (opening.HISTORY,context['history_sha256']),(opening.ROLE,context['opening_confirmation_sha256'])):
                if futures.digest(session.drafts.document(draft.id,role)[2])!=expected:
                    raise ValueError('Futures statement source post-write verification failed')
            return result

    def futures_preview(self,user,account_id,draft_id,revision):
        from campaigniq.account_futures_preview import connected_preview
        if type(revision) is not int or revision<0:raise ValueError('Displayed revision required')
        with self.gateway.transaction(user,account_id) as session:
            draft=self._lock(session,draft_id)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before futures preview')
            return connected_preview(self,session,draft)

    def confirm_empty_futures_closing(self,user,account_id,draft_id,revision,*,confirm_empty_closing=False):
        import json
        from campaigniq import account_futures_closing as closing
        from campaigniq.account_futures_preview import connected_preview
        if confirm_empty_closing is not True or type(revision) is not int or revision<0:
            raise ValueError('Explicit empty-ending confirmation and displayed revision required')
        with self.gateway.transaction(user,account_id,write=True) as session:
            draft=self._lock(session,draft_id,write=True)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before confirming ending positions')
            if session.artifacts.list_keys(prefix=f'{draft.year:04d}-{draft.month:02d}-'):
                raise ValueError('Published month ending confirmations are preserved')
            directory,actor=DirectoryTransaction(session._connection,self.gateway._directory.settings)._actor(user)
            directory.authorize(actor.identity,account_id,write=True)
            account=next(a for a in directory.accounts if a.id==account_id)
            if account.owner_id!=actor.user_id:raise PermissionError('Only the account owner can confirm ending positions')
            prior=closing.load_receipt(session,draft)
            result=connected_preview(self,session,draft,include_closing=False)
            receipt=closing.make_receipt(session,draft,actor.user_id,result)
            if prior is not None and prior['evidence_graph_sha256']==receipt['evidence_graph_sha256']:
                return draft.revision
            revision=session.drafts.upload(draft.id,closing.ROLE,'futures-empty-closing-confirmation.json','text/plain',
                (json.dumps(receipt,sort_keys=True,indent=2)+'\n').encode(),replace=prior is not None)
            saved=session.drafts.get(draft.id)
            if closing.load_receipt(session,saved)!=receipt:
                raise ValueError('Futures ending confirmation post-write verification failed')
            # Re-read source links and calculation within the same transaction.
            checked=connected_preview(self,session,saved)
            if checked['closing_positions_confirmed_by_owner'] is not True:
                raise ValueError('Futures ending confirmation could not be verified')
            return revision

    def futures_monthly_preview(self,user,account_id,draft_id,revision,*,forex_applicable=True,crypto_applicable=True):
        from campaigniq.futures_monthly_preview import monthly_preview
        if type(revision) is not int or revision<0 or any(type(v) is not bool for v in (forex_applicable,crypto_applicable)):
            raise ValueError('Displayed revision and explicit market choices required')
        with self.gateway.transaction(user,account_id) as session:
            draft=self._lock(session,draft_id)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before monthly futures preview')
            return monthly_preview(self,session,draft,forex_applicable=forex_applicable,crypto_applicable=crypto_applicable)

    def futures_generation_preview(self,user,account_id,draft_id,revision):
        from campaigniq.account_futures_generation import generation_preview
        if type(revision) is not int or revision<0:raise ValueError('Displayed revision required')
        with self.gateway.transaction(user,account_id) as session:
            draft=self._lock(session,draft_id)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before generation preview')
            return generation_preview(self,session,draft)

    def review_crypto_statements(self,user,account_id,draft_id,revision,previous_upload,current_upload):
        from campaigniq import account_crypto_statement_sources as crypto
        if type(revision) is not int or revision<0:raise ValueError('Displayed revision required')
        previous=crypto.upload_bytes(previous_upload);current=crypto.upload_bytes(current_upload)
        with self.gateway.transaction(user,account_id) as session:
            draft=self._lock(session,draft_id)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before crypto source review')
            context,body=crypto.evaluate(self,session,draft,previous,current)
            return dict(account_id=account_id,draft_id=str(draft.id),revision=draft.revision,
                reviewed_preview_sha256=context['preview_sha256'],calculation=body,publication_authorized=False)

    def save_crypto_statement_sources(self,user,account_id,draft_id,revision,previous_upload,current_upload,*,
            reviewed_preview_sha256,confirm_same_crypto_account=False,confirm_source_choices=False,replace=False):
        from campaigniq import account_crypto_statement_sources as crypto
        import json
        if (confirm_same_crypto_account is not True or confirm_source_choices is not True or
            type(revision) is not int or revision<0 or type(replace) is not bool or not crypto._hash(reviewed_preview_sha256)):
            raise ValueError('Current source review, account association, source choices and displayed revision required')
        previous=crypto.upload_bytes(previous_upload);current=crypto.upload_bytes(current_upload)
        with self.gateway.transaction(user,account_id,write=True) as session:
            draft=self._lock(session,draft_id,write=True)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before saving crypto sources')
            if session.artifacts.list_keys(prefix=f'{draft.year:04d}-{draft.month:02d}-'):
                raise ValueError('Published month crypto originals and source choices are preserved')
            directory,actor=DirectoryTransaction(session._connection,self.gateway._directory.settings)._actor(user)
            directory.authorize(actor.identity,account_id,write=True)
            account=next(a for a in directory.accounts if a.id==account_id)
            if account.owner_id!=actor.user_id:raise PermissionError('Only the account owner can associate crypto sources and source choices')
            prior=crypto.load_receipt(session,draft);originals=crypto.originals(session,draft,prior)
            context,_=crypto.evaluate(self,session,draft,previous,current)
            if context['preview_sha256']!=reviewed_preview_sha256:
                raise ValueError('Crypto source review differs from the current exact documents')
            if prior is None and crypto.CURRENT in originals and originals[crypto.CURRENT]!=current and not replace:
                raise ValueError('Confirm replacement of the existing unapproved current crypto statement')
            if prior is not None:
                if any(prior[key]!=context[key] for key in ('binding_digest','crypto_account_digest')):
                    raise ValueError('Broker account identity changed; separate account review required')
                if all(prior[key]==value for key,value in context.items()):return draft.revision
                if not replace:raise ValueError('Confirm replacement; prior crypto originals and receipt are preserved')
            receipt=crypto.make_receipt(session,draft,actor.user_id,context)
            for role,upload,content in ((crypto.PREVIOUS,previous_upload,previous),(crypto.CURRENT,current_upload,current)):
                if originals.get(role)!=content:
                    session.drafts.upload(draft.id,role,upload.name,'application/pdf',content,replace=role in originals)
            revision=session.drafts.upload(draft.id,crypto.RECEIPT,'crypto-source-choice-receipt.json','text/plain',
                (json.dumps(receipt,sort_keys=True,indent=2)+'\n').encode(),replace=prior is not None)
            saved=session.drafts.get(draft.id)
            if crypto.load_receipt(session,saved)!=receipt:raise ValueError('Crypto source receipt post-write verification failed')
            crypto.saved_preview(self,session,saved)
            return revision

    def crypto_statement_preview(self,user,account_id,draft_id,revision):
        from campaigniq.account_crypto_statement_sources import saved_preview
        if type(revision) is not int or revision<0:raise ValueError('Displayed revision required')
        with self.gateway.transaction(user,account_id) as session:
            draft=self._lock(session,draft_id)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before crypto preview')
            return saved_preview(self,session,draft)

    def crypto_generation_preview(self,user,account_id,draft_id,revision):
        from campaigniq.account_crypto_generation import generation_preview
        if type(revision) is not int or revision<0:raise ValueError('Displayed revision required')
        with self.gateway.transaction(user,account_id) as session:
            draft=self._lock(session,draft_id)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before crypto generation preview')
            return generation_preview(self,session,draft)
