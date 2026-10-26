"""Read-only closing-inventory preview for an authorized synthetic account."""
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from campaigniq.account_import_preview import BoundAccountImportPreviewService
from campaigniq.import_draft_bridge import restore_uploads
from campaigniq.import_preflight import prepare_monthly_import
from campaigniq.monthly_upload_staging import stage_monthly_uploads


class ReadOnlyArtifacts:
    """Deny writes even if a future execution refactor crosses the preview boundary."""
    def __init__(self,storage):self._storage=storage
    def exists(self,key):return self._storage.exists(key)
    def read_text(self,key):return self._storage.read_text(key)
    def list_keys(self,*,prefix='',suffix=''):
        return self._storage.list_keys(prefix=prefix,suffix=suffix)
    def write_text(self,*args,**kwargs):raise PermissionError('Reconciliation preview cannot write artifacts')
    def delete(self,*args,**kwargs):raise PermissionError('Reconciliation preview cannot delete artifacts')


@dataclass(frozen=True)
class QuantityDifference:
    instrument:str
    computed:str
    statement:str
    difference:str


@dataclass(frozen=True)
class AccountReconciliationPreview:
    account_id:str
    draft_id:str
    revision:int
    inventory_reconciled:bool
    mismatches:tuple[QuantityDifference,...]
    source_findings:tuple[str,...]
    closing_quantities:tuple[tuple[str,str],...]
    publication_authorized:bool=False


def reconcile_saved(service,identity,account_id,draft_id,revision,*,forex_applicable=True,crypto_applicable=True):
    if type(service) is not BoundAccountImportPreviewService:
        raise ValueError('Bound local account preview service required')
    if type(revision) is not int or any(type(v) is not bool for v in (forex_applicable,crypto_applicable)):
        raise ValueError('Displayed draft revision and boolean market choices required')
    with service.gateway.transaction(identity,account_id) as session:
        draft=service._lock(session,draft_id)
        if draft.revision!=revision:raise ValueError('Draft changed; reload before reconciliation')
        uploads=restore_uploads(session.drafts,draft.id)
        with TemporaryDirectory(prefix='campaigniq-account-reconciliation-') as directory:
            root=Path(directory)
            supplied=stage_monthly_uploads(upload_dir=root,uploads=uploads)
            identity_checks=service._identity_validations(session,uploads,supplied)
            if any(not check.valid for check in identity_checks):
                raise ValueError('Document account identity is unverified; review Preview saved inputs first')
            storage=ReadOnlyArtifacts(session.artifacts)
            preflight=prepare_monthly_import(draft.year,draft.month,
                authoritative_state_root=root/'state',supplied_inputs=supplied,artifact_storage=storage,
                forex_applicable=forex_applicable,crypto_applicable=crypto_applicable)
            if not preflight.ready:
                raise ValueError('Required inputs are incomplete or invalid; review Preview saved inputs first')
            from campaigniq.monthly_import_execution import execute_monthly_import
            outcome=execute_monthly_import(preflight,authoritative_state_root=root/'state',
                supplied_inputs=supplied,artifact_storage=storage,preview_only=True)
            if outcome.finalized:raise ValueError('Unexpected finalization in read-only preview')
            control=outcome.closing_reconciliation
            quantities=[]
            for instrument,lots in outcome.result.ending_lot_book._lots.items():
                quantity=sum(lot.quantity for lot in lots)
                if quantity:quantities.append((repr(instrument),str(quantity)))
            return AccountReconciliationPreview(account_id,str(draft.id),draft.revision,
                control.reconciled and not control.forex_source_findings,
                tuple(QuantityDifference(repr(row.instrument),str(row.computed_quantity),
                    str(row.snapshot_quantity),str(row.difference)) for row in control.mismatches),
                tuple(control.forex_source_findings),tuple(sorted(quantities)))
