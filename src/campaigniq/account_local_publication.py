"""Explicit synthetic LOCAL publication rehearsal API; never wired to the browser."""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date,timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from campaigniq.account_import_preview import BoundAccountImportPreviewService
from campaigniq.historical_backfill_guard import assess_backfill
from campaigniq.import_draft_bridge import restore_uploads
from campaigniq.import_preflight import prepare_monthly_import
from campaigniq.monthly_upload_staging import stage_monthly_uploads
from campaigniq.persistence.lot_book_store import load_lot_book_from_storage
from campaigniq.persistence.monthly_publication import is_month_published_in_storage


@dataclass(frozen=True)
class SyntheticPublication:
    account_id:str
    year:int
    month:int
    draft_id:str
    revision:int
    artifact_keys:tuple[str,...]
    receipt_sha256:str
    local_published:bool=True
    production_publication_authorized:bool=False


def _periods(storage):
    result=[]
    for key in storage.list_keys(suffix='-lot-book.json'):
        match=re.fullmatch(r'([0-9]{4})-([0-9]{2})-lot-book.json',key)
        if match is None:raise ValueError('Unexpected lot-state key; publication stopped')
        year,month=map(int,match.groups())
        end=date(year,month,monthrange(year,month)[1])
        if load_lot_book_from_storage(storage,key).period_end!=end:
            raise ValueError('Lot-state period differs from its key; publication stopped')
        result.append(end)
    return tuple(sorted(result))


def publish_synthetic_month(service,identity,account_id,draft_id,revision,*,
                            authorize_synthetic_publication=False,forex_applicable=True,crypto_applicable=True):
    if authorize_synthetic_publication is not True:
        raise PermissionError('Explicit synthetic LOCAL publication authorization required')
    if type(service) is not BoundAccountImportPreviewService:
        raise ValueError('Bound local account service required')
    if type(revision) is not int or any(type(flag) is not bool for flag in (forex_applicable,crypto_applicable)):
        raise ValueError('Displayed draft revision and explicit market choices required')
    # The gateway verifies peer/local campaigniq_dev and this registry's synthetic
    # identity on every connection. The write path is inaccessible to the UI.
    with service.gateway.transaction(identity,account_id,write=True) as session:
        with session._operation() as conn:
            # Serialize this publisher across all drafts for the same account.
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(session.artifacts.namespace,))
        draft=service._lock(session,draft_id,write=True)
        if draft.revision!=revision:raise ValueError('Draft changed; reload before publication')
        start=date(draft.year,draft.month,1)
        end=date(draft.year,draft.month,monthrange(draft.year,draft.month)[1])
        existing=_periods(session.artifacts)
        if not existing and session.artifacts.list_keys():
            raise ValueError('Account storage has unreviewed artifacts; publication stopped')
        guard=assess_backfill(end,existing)
        if guard.blocked:raise ValueError(guard.message)
        if existing and (existing[-1]!=start-timedelta(days=1)
                         or not is_month_published_in_storage(session.artifacts,period_end=existing[-1])):
            raise ValueError('Synthetic publisher requires the exact preceding published month')
        prefix=f'{draft.year:04d}-{draft.month:02d}-'
        if session.artifacts.list_keys(prefix=prefix):
            raise ValueError('Existing artifacts for this month must be preserved; publication stopped')
        uploads=restore_uploads(session.drafts,draft.id)
        originals={str(getattr(role,'value',role)):dict(sha256=sha256(upload.content).hexdigest(),byte_size=len(upload.content))
                   for role,upload in uploads.items()}
        directory=service.gateway._directory._load(session._connection)
        actor=directory.user(identity)
        with TemporaryDirectory(prefix='campaigniq-synthetic-publication-') as directory_name:
            root=Path(directory_name)
            supplied=stage_monthly_uploads(upload_dir=root,uploads=uploads)
            checks=service._identity_validations(session,uploads,supplied)
            if any(not row.valid for row in checks):raise ValueError('Document account identity is unverified')
            preflight=prepare_monthly_import(draft.year,draft.month,authoritative_state_root=root/'state',
                supplied_inputs=supplied,artifact_storage=session.artifacts,
                forex_applicable=forex_applicable,crypto_applicable=crypto_applicable)
            if not preflight.ready:raise ValueError('Required inputs are incomplete or invalid')
            from campaigniq.monthly_import_execution import execute_monthly_import
            # No reconciliation exception, history reseeding or filesystem archive.
            # Every canonical artifact and visibility marker use one SQL connection.
            outcome=execute_monthly_import(preflight,authoritative_state_root=root/'state',
                supplied_inputs=supplied,artifact_storage=session.artifacts)
            if not outcome.finalized or not outcome.closing_reconciliation.reconciled:
                raise ValueError('Closing inventory does not reconcile; synthetic publication rejected')
            if outcome.closing_reconciliation.forex_source_findings:
                raise ValueError('Forex source controls remain unresolved')
            receipt=dict(format='campaigniq.synthetic_account_publication',version=1,
                registry_id=service.gateway._directory.registry_id,account_id=account_id,actor_id=actor.id,
                draft_id=str(draft.id),draft_revision=draft.revision,period_end=end.isoformat(),
                original_documents=originals,production_publication_authorized=False)
            text=json.dumps(receipt,sort_keys=True,indent=2)+'\n'
            session.artifacts.write_text(prefix+'account-publication-receipt.json',text)
            lot_key=prefix+'lot-book.json'
            if (not is_month_published_in_storage(session.artifacts,period_end=end)
                    or load_lot_book_from_storage(session.artifacts,lot_key).period_end!=end):
                raise ValueError('Staged generation verification failed')
            return SyntheticPublication(account_id,draft.year,draft.month,str(draft.id),draft.revision,
                session.artifacts.list_keys(prefix=prefix),sha256(text.encode()).hexdigest())
