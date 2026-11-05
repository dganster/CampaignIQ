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
        if draft is not None:
            from campaigniq.account_crypto_statement_sources import CURRENT,load_receipt as crypto_receipt,saved_preview
            if crypto_receipt(session,draft) is not None:
                saved_preview(self,session,draft)
                checks=tuple(InputStatus('account:'+CURRENT,True,'Crypto account association confirmed by owner against exact approved originals; not broker verified.')
                    if item.role=='account:'+CURRENT else item for item in checks)
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

    def save_forex_daily_sources(self,user,account_id,draft_id,revision,daily_reports,*,
            reviewed_preview_sha256,confirm_same_account=False,confirm_coverage_limits=False,replace=False):
        from campaigniq import account_forex_daily_inventory as daily
        from campaigniq import account_forex_daily_sources as sources
        from campaigniq import account_forex_cash_review as cash
        import re
        if (confirm_same_account is not True or confirm_coverage_limits is not True or type(replace) is not bool
            or type(revision) is not int or revision<0 or not isinstance(reviewed_preview_sha256,str)
            or re.fullmatch('[0-9a-f]{64}',reviewed_preview_sha256) is None):
            raise ValueError('Current daily review, revision and explicit association/coverage choices required')
        raw=daily.archive_bytes(daily_reports)
        pdfs=daily.originals(raw)
        with self.gateway.transaction(user,account_id,write=True) as session:
            draft=self._lock(session,draft_id,write=True)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before saving daily originals')
            if session.artifacts.list_keys(prefix=f'{draft.year:04d}-{draft.month:02d}-'):
                raise ValueError('Published month daily originals are preserved')
            directory,actor=DirectoryTransaction(session._connection,self.gateway._directory.settings)._actor(user)
            directory.authorize(actor.identity,account_id,write=True)
            account=next(a for a in directory.accounts if a.id==account_id)
            if account.owner_id!=actor.user_id:raise PermissionError('Only the account owner can confirm daily Forex sources')
            prior=sources.load_receipt(session,draft)
            previous=sources.saved_originals(session,draft,prior)
            if prior is not None:
                sources.saved_preview(self,session,draft)
            result=daily.evaluate(self,session,draft,raw)
            if result['review_sha256']!=reviewed_preview_sha256:
                raise ValueError('Daily review differs from current source bytes or revision')
            ctx=sources.context(self,session,result,pdfs)
            if prior is not None:
                if all(prior[key]==value for key,value in ctx.items()):return draft.revision
                if not replace:raise ValueError('Confirm daily source replacement; earlier originals remain preserved')
                if not {item['date'] for item in prior['pdf_inventory']}<={item['date'] for item in ctx['pdf_inventory']}:
                    raise ValueError('Replacement cannot remove previously saved daily report dates; separate source review required')
            receipt=sources.make_receipt(session,draft,actor.user_id,ctx)
            by_hash={sources.digest(content):content for name,content in pdfs}
            prior_hash={item['role']:item['pdf_sha256'] for item in prior['pdf_inventory']} if prior else {}
            for item in ctx['pdf_inventory']:
                if prior_hash.get(item['role'])!=item['pdf_sha256']:
                    session.drafts.upload(draft.id,item['role'],item['stored_filename'],'application/pdf',
                        by_hash[item['pdf_sha256']],replace=item['role'] in prior_hash)
            saved_revision=session.drafts.upload(draft.id,sources.RECEIPT,'forex-daily-owner-receipt.json','text/plain',
                cash.canonical(receipt)+b'\n',replace=prior is not None)
            current=session.drafts.get(draft.id)
            if sources.load_receipt(session,current)!=receipt:raise ValueError('Daily source receipt post-write verification failed')
            sources.saved_preview(self,session,current)
            return saved_revision

    def forex_daily_source_preview(self,user,account_id,draft_id,revision):
        from campaigniq.account_forex_daily_sources import saved_preview
        if type(revision) is not int or revision<0:raise ValueError('Displayed draft revision required')
        with self.gateway.transaction(user,account_id) as session:
            draft=self._lock(session,draft_id)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before saved daily review')
            return saved_preview(self,session,draft)

    def forex_daily_inventory_preview(self,user,account_id,draft_id,revision,daily_reports):
        from campaigniq import account_forex_daily_inventory as daily
        if type(revision) is not int or revision<0:
            raise ValueError('Displayed draft revision required')
        raw=daily.archive_bytes(daily_reports)
        with self.gateway.transaction(user,account_id) as session:
            draft=self._lock(session,draft_id)
            if draft.revision!=revision:
                raise ValueError('Draft changed; reload before daily Forex review')
            return daily.evaluate(self,session,draft,raw)

    def forex_inventory_preview(self,user,account_id,draft_id,revision,monthly_statement):
        from campaigniq import account_forex_inventory_preview as inventory
        if type(revision) is not int or revision<0:
            raise ValueError('Displayed draft revision required')
        raw=inventory.pdf_bytes(monthly_statement)
        with self.gateway.transaction(user,account_id) as session:
            draft=self._lock(session,draft_id)
            if draft.revision!=revision:
                raise ValueError('Draft changed; reload before Forex inventory review')
            return inventory.evaluate(self,session,draft,raw)

    def review_forex_cash_sources(self,user,account_id,draft_id,revision,cash_upload,supplemental_upload):
        from campaigniq import account_forex_cash_review as cash
        if type(revision) is not int or revision<0:
            raise ValueError('Displayed draft revision required')
        ledger=cash.upload_bytes(cash_upload);supplement=cash.upload_bytes(supplemental_upload)
        with self.gateway.transaction(user,account_id) as session:
            draft=self._lock(session,draft_id)
            if draft.revision!=revision:
                raise ValueError('Draft changed; reload before Forex cash review')
            return cash.evaluate(self,session,draft,ledger,supplement)

    def save_forex_cash_sources(self,user,account_id,draft_id,revision,cash_upload,supplemental_upload,*,
            reviewed_preview_sha256,confirm_same_account=False,confirm_source_choices=False,replace=False):
        from campaigniq import account_forex_cash_review as cash
        import re
        if (confirm_same_account is not True or confirm_source_choices is not True or type(replace) is not bool
            or type(revision) is not int or revision<0 or not isinstance(reviewed_preview_sha256,str)
            or re.fullmatch('[0-9a-f]{64}',reviewed_preview_sha256) is None):
            raise ValueError('Current Forex review, revision and explicit source choices required')
        ledger=cash.upload_bytes(cash_upload);supplement=cash.upload_bytes(supplemental_upload)
        with self.gateway.transaction(user,account_id,write=True) as session:
            draft=self._lock(session,draft_id,write=True)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before saving Forex sources')
            if session.artifacts.list_keys(prefix=f'{draft.year:04d}-{draft.month:02d}-'):
                raise ValueError('Published month Forex sources are preserved')
            directory,actor=DirectoryTransaction(session._connection,self.gateway._directory.settings)._actor(user)
            directory.authorize(actor.identity,account_id,write=True)
            account=next(a for a in directory.accounts if a.id==account_id)
            if account.owner_id!=actor.user_id:raise PermissionError('Only the account owner can confirm Forex sources')
            prior=cash.load_receipt(session,draft);docs=cash.originals(session,draft,prior)
            result=cash.evaluate(self,session,draft,ledger,supplement)
            ctx=cash.context(self,session,result)
            if ctx['review_sha256']!=reviewed_preview_sha256:
                raise ValueError('Forex review differs from current exact sources')
            if prior is not None:
                if prior['binding_sha256']!=ctx['binding_sha256']:
                    raise ValueError('Broker binding changed; separate account review required')
                if all(prior[k]==v for k,v in ctx.items()):return draft.revision
                if not replace:raise ValueError('Confirm replacement; prior Forex originals remain preserved')
            receipt=cash.make_receipt(session,draft,actor.user_id,ctx)
            for role,upload,raw in ((cash.CASH,cash_upload,ledger),(cash.SUPPLEMENT,supplemental_upload,supplement)):
                if docs.get(role)!=raw:
                    session.drafts.upload(draft.id,role,upload.name,'text/csv',raw,replace=role in docs)
            revision=session.drafts.upload(draft.id,cash.RECEIPT,'forex-cash-source-receipt.json','text/plain',
                cash.canonical(receipt)+b'\n',replace=prior is not None)
            saved=session.drafts.get(draft.id)
            if cash.load_receipt(session,saved)!=receipt:raise ValueError('Forex receipt verification failed')
            cash.saved_preview(self,session,saved)
            return revision

    def forex_cash_source_preview(self,user,account_id,draft_id,revision):
        from campaigniq.account_forex_cash_review import saved_preview
        if type(revision) is not int or revision<0:raise ValueError('Displayed draft revision required')
        with self.gateway.transaction(user,account_id) as session:
            draft=self._lock(session,draft_id)
            if draft.revision!=revision:raise ValueError('Draft changed; reload before saved Forex review')
            return saved_preview(self,session,draft)

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
