"""Local synthetic-account draft service. Input preflight only; no publication API."""
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from campaigniq.import_contract import MonthlyInputRole
from campaigniq.import_draft_bridge import SUPPORTED_UPLOAD_KEYS, restore_uploads
from campaigniq.import_preflight import prepare_monthly_import
from campaigniq.persistence.account_data_session import LocalAccountDataGateway


UPLOAD_ROLES = tuple(sorted(str(getattr(role, 'value', role)) for role in SUPPORTED_UPLOAD_KEYS))


@dataclass(frozen=True)
class InputStatus:
    role: str
    valid: bool
    message: str


@dataclass(frozen=True)
class AccountInputPreview:
    account_id: str
    draft_id: str
    revision: int
    year: int
    month: int
    inputs_ready: bool
    validations: tuple[InputStatus, ...]
    publication_authorized: bool = False
    inventory_reconciled: bool = False


class AccountImportPreviewService:
    def __init__(self, gateway):
        if type(gateway) is not LocalAccountDataGateway:
            raise ValueError('Guarded local account gateway required')
        self.gateway = gateway

    @staticmethod
    def _lock(session, draft_id, *, write=False):
        # Use the existing draft row lock before checking the displayed revision.
        # Uploads also lock this row; a competing upload cannot invalidate a preview.
        with session._operation() as conn:
            conn.execute('SELECT id FROM import_drafts WHERE id=%s AND workspace=%s AND state=\'draft\' FOR ' +
                         ('UPDATE' if write else 'SHARE'), (draft_id, session.drafts.workspace)).fetchone()
        return session.drafts.get(draft_id)

    def _validate_upload(self, session, role, upload):
        # Compatibility service for the earlier explicitly unbound test harness.
        # The interactive preview uses BoundAccountImportPreviewService below.
        pass

    def _identity_validations(self, session, uploads, supplied, *, draft=None):
        return ()

    def drafts(self, identity, account_id):
        with self.gateway.transaction(identity, account_id) as session:
            return session.drafts.list_drafts()

    def create(self, identity, account_id, year, month):
        if type(year) is not int or type(month) is not int:
            raise ValueError('Integer year and month required')
        with self.gateway.transaction(identity, account_id, write=True) as session:
            return session.drafts.create_or_resume(year, month)

    def save(self, identity, account_id, draft_id, revision, role, upload, *, replace=False):
        if role not in UPLOAD_ROLES or type(replace) is not bool:
            raise ValueError('Known document role and explicit replacement choice required')
        if type(revision) is not int:
            raise ValueError('Displayed draft revision required')
        with self.gateway.transaction(identity, account_id, write=True) as session:
            draft = self._lock(session, draft_id, write=True)
            if draft.revision != revision:
                raise ValueError('Draft changed; reload before saving')
            self._validate_upload(session, role, upload)
            return session.drafts.upload(draft.id, role, upload.name, upload.media_type,
                                         upload.getvalue(), replace=replace)

    def preview(self, identity, account_id, draft_id, revision, *, forex_applicable=True, crypto_applicable=True):
        if type(revision) is not int or any(type(flag) is not bool for flag in (forex_applicable, crypto_applicable)):
            raise ValueError('Displayed revision and boolean market choices required')
        with self.gateway.transaction(identity, account_id) as session:
            draft = self._lock(session, draft_id)
            if draft.revision != revision:
                raise ValueError('Draft changed; reload before previewing')
            uploads = restore_uploads(session.drafts, draft.id)
            # Import only pure staging code. The dashboard module starts its runtime
            # and renders UI when imported under Streamlit. Never import it here.
            from campaigniq.monthly_upload_staging import stage_monthly_uploads
            with TemporaryDirectory(prefix='campaigniq-account-preview-') as directory:
                root = Path(directory)
                supplied = stage_monthly_uploads(upload_dir=root, uploads=uploads)
                identity_checks = self._identity_validations(session, uploads, supplied, draft=draft)
                result = prepare_monthly_import(draft.year, draft.month,
                    authoritative_state_root=root / 'unused-authoritative-state',
                    supplied_inputs=supplied, artifact_storage=session.artifacts,
                    forex_applicable=forex_applicable, crypto_applicable=crypto_applicable)
                # Return only immutable summaries. No temporary paths, live storage
                # handles, or mutable lot books can escape this transaction.
                return AccountInputPreview(account_id, str(draft.id), draft.revision, draft.year, draft.month,
                    result.ready and all(v.valid for v in identity_checks),
                    tuple(InputStatus(str(v.role.value), v.valid, v.message) for v in result.validations) + identity_checks)


class BoundAccountImportPreviewService(AccountImportPreviewService):
    """Interactive preview: binding checks run under the account transaction lock."""
    def _known(self, session):
        from campaigniq.broker_account_binding import bindings
        directory=self.gateway._directory._load(session._connection)
        return bindings(directory,session.account.account_id)

    @staticmethod
    def _path(role,supplied):
        key=MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT if role=='schwab_brokerage_statement' else MonthlyInputRole(role)
        return supplied[key]

    def _validate_upload(self,session,role,upload):
        from campaigniq.broker_account_binding import verify_document
        from campaigniq.monthly_upload_staging import stage_monthly_uploads
        # Apply the existing size/type checks before extraction, too.
        from campaigniq.persistence.import_drafts import MAX_DOCUMENT_BYTES
        content=upload.getvalue()
        if (not content or len(content)>MAX_DOCUMENT_BYTES
                or upload.media_type not in ('application/pdf','text/csv','text/plain','application/vnd.ms-excel')):
            raise ValueError('Document must have a supported type and be nonempty, at most 20 MiB')
        with TemporaryDirectory(prefix='campaigniq-bound-upload-') as directory:
            supplied=stage_monthly_uploads(upload_dir=Path(directory),uploads={role:upload})
            verify_document(self._known(session),role,self._path(role,supplied).read_text(encoding='utf-8-sig'))

    def _identity_validations(self,session,uploads,supplied,*,draft=None):
        from campaigniq.broker_account_binding import verify_document
        known=self._known(session)
        checks=[]
        if 'securities' not in known:
            checks.append(InputStatus('broker_account_binding',False,'An administrator must bind the securities account identifier.'))
        for role in uploads:
            name=str(getattr(role,'value',role))
            try:
                kind=verify_document(known,name,self._path(name,supplied).read_text(encoding='utf-8-sig'))
                checks.append(InputStatus('account:'+name,True,kind.capitalize()+' account identity matches the selected account.'))
            except ValueError as error:
                checks.append(InputStatus('account:'+name,False,str(error)))
        return tuple(checks)
