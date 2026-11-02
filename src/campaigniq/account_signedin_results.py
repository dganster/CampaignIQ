"""Read published LOCAL new-account results in one read-only SQL snapshot."""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json

from campaigniq.broker_report_period import validate_broker_report_date
from campaigniq.account_import_preview import AccountImportPreviewService
from campaigniq.account_reconciliation_preview import ReadOnlyArtifacts
from campaigniq.account_signedin_inputs import SignedInMonthlyInputs
from campaigniq.account_signedin_publication import _published_session
from campaigniq.domain.option_contract import OptionContract
from campaigniq.import_draft_bridge import restore_uploads
from campaigniq.persistence.account_data_session import AccountDataSession
from campaigniq.persistence.lot_book_store import load_lot_book_from_storage
from campaigniq.persistence.realized_attribution_store import load_realized_attributions_from_storage
from campaigniq.persistence.forex_settlement_attribution_store import load_forex_settlement_attributions_from_storage


@dataclass(frozen=True)
class PublishedResults:
    account_id: str
    draft_id: str
    revision: int
    month: str
    positions: tuple[tuple[str,str], ...]
    lots: tuple[tuple[str,str,str,str,str,str], ...]
    realized: tuple[tuple[str,str,str,str,str,str], ...]
    brokerage_realized_usd: str
    forex_settlements: tuple[tuple[str,str,str,str], ...]
    forex_settled_usd: str
    forex_applicable: bool
    crypto_applicable: bool
    crypto_positions: tuple[tuple[str,str], ...]
    crypto_status: str
    crypto_warnings: tuple[str, ...]
    original_documents: int
    publication_receipt_sha256: str
    futures_applicable: bool=False
    futures_realized_usd: str="0"
    futures_evidence_status: str="Not applicable"


def _instrument(value):
    if isinstance(value,OptionContract):
        return f'{value.underlying} {value.expiration.isoformat()} {value.option_type.value} {value.strike}'
    return value.symbol


def _decimal(value):
    result=Decimal(value)
    if not result.is_finite():raise ValueError('Non-finite published amount')
    return result


def _results_session(service,session,draft,account_id,revision,*,receipt_format='campaigniq.signedin_local_account_publication'):
    if not _published_session(service,session,account_id,draft,revision,receipt_format=receipt_format):
        raise ValueError('This saved month has not been published locally')
    month=f'{draft.year:04d}-{draft.month:02d}'
    prefix=month+'-'
    start=date(draft.year,draft.month,1)
    end=date(draft.year,draft.month,monthrange(draft.year,draft.month)[1])
    receipt_text=session.artifacts.read_text(prefix+'account-publication-receipt.json')
    receipt=json.loads(receipt_text)
    uploads=restore_uploads(session.drafts,draft.id)
    originals={str(getattr(role,'value',role)):dict(sha256=sha256(upload.content).hexdigest(),byte_size=len(upload.content))
        for role,upload in uploads.items()}
    if receipt.get('original_documents')!=originals:
        raise ValueError('Retained originals differ from the publication receipt')
    from campaigniq.account_gain_loss_association import load_receipt
    if receipt.get('gain_loss_account_confirmation')!=load_receipt(session,draft):
        raise ValueError('Report confirmation differs from the publication receipt')
    if any(type(receipt.get(flag)) is not bool for flag in ('forex_applicable','crypto_applicable')):
        raise ValueError('Published market choices are missing or invalid')
    markets=json.loads(session.artifacts.read_text(prefix+'market-applicability.json'))
    if (markets.get('format')!='campaigniq.market_applicability' or markets.get('version')!=1
        or markets.get('period_end')!=end.isoformat()
        or any(markets.get(flag) is not receipt[flag] for flag in ('forex_applicable','crypto_applicable'))):
        raise ValueError('Published market choices differ from the source receipt')
    state=load_lot_book_from_storage(session.artifacts,prefix+'lot-book.json')
    quantities={};lots=[]
    for rows in state.lot_book._lots.values():
        for lot in rows:
            name=_instrument(lot.instrument)
            quantities[name]=quantities.get(name,Decimal(0))+_decimal(lot.quantity)
            if lot.basis_total is not None:_decimal(lot.basis_total)
            lots.append((name,str(lot.quantity),lot.lot_id,
                str(lot.basis_total) if lot.basis_total is not None else 'Unknown',
                lot.basis_source or 'Unknown',lot.campaign_id or 'Unassigned'))
    realized=load_realized_attributions_from_storage(session.artifacts,prefix+'realized-attributions.json')
    forex=load_forex_settlement_attributions_from_storage(session.artifacts,prefix+'forex-settlement-attributions.json')
    if any(item.period_start!=start or item.period_end!=end for item in (realized,forex)):
        raise ValueError('Published monetary artifact period differs from the selected month')
    broker_rows=[];broker_total=Decimal(0)
    for attribution in realized.attributions:
        row=attribution.record
        validate_broker_report_date(row.closed_date,period_start=start,period_end=end)
        for value in (row.quantity,row.proceeds,row.cost_basis,row.gain_loss):_decimal(value)
        broker_rows.append((row.closed_date.isoformat(),_instrument(row.instrument),str(row.quantity),
            str(row.proceeds),str(row.cost_basis),str(row.gain_loss)))
        broker_total+=row.gain_loss
    fx_rows=[];fx_total=Decimal(0)
    for attribution in forex.attributions:
        row=attribution.settlement
        value=_decimal(row.settlement_pl_usd);fx_total+=value
        fx_rows.append((str(row.order_id),row.instrument,str(value),attribution.campaign_id or 'Unassigned'))
    if not receipt['forex_applicable'] and fx_rows:
        raise ValueError('Forex records conflict with published market choices')
    crypto_positions=();crypto_status='Not applicable';crypto_warnings=()
    crypto_key=prefix+'crypto.json'
    if receipt['crypto_applicable']:
        crypto=json.loads(session.artifacts.read_text(crypto_key))
        if (crypto.get('format')!='campaigniq.crypto_month' or crypto.get('version')!=1
            or crypto.get('period_end')!=end.isoformat()):
            raise ValueError('Unsupported or wrong-period published crypto evidence')
        crypto_positions=tuple(sorted((pair,str(_decimal(quantity))) for pair,quantity in crypto['ending_quantities'].items()))
        crypto_status=str(crypto['status'])
        if not isinstance(crypto.get('warnings'),list) or any(not isinstance(w,str) for w in crypto['warnings']):
            raise ValueError('Invalid published crypto warnings')
        crypto_warnings=tuple(crypto['warnings'])
    elif session.artifacts.exists(crypto_key):
        raise ValueError('Crypto artifact conflicts with published market choices')
    return PublishedResults(account_id,str(draft.id),revision,month,tuple(sorted((k,str(v)) for k,v in quantities.items())),
        tuple(sorted(lots)),tuple(broker_rows),str(broker_total),tuple(fx_rows),str(fx_total),
        receipt['forex_applicable'],receipt['crypto_applicable'],crypto_positions,crypto_status,crypto_warnings,
        len(originals),sha256(receipt_text.encode()).hexdigest())


def load_published_results(service,user,account_id,draft_id,revision):
    if type(service) is not SignedInMonthlyInputs or type(revision) is not int:
        raise ValueError('Signed-in service and displayed source revision required')
    gateway=service.gateway
    # Lock authorization/source rows outside the READ ONLY snapshot. The
    # actual result loaders cannot write, even through injected raw SQL.
    with gateway.transaction(user,account_id) as guard:
        draft=AccountImportPreviewService._lock(guard,draft_id)
        if draft.revision!=revision:raise ValueError('Draft changed; reload before viewing results')
        session=None
        with gateway._directory._connection(readonly=True) as conn:
            directory,actor=gateway._current(conn,user)
            access=directory.authorize(actor.identity,account_id)
            if not gateway._ready(conn):raise ValueError('Dedicated local input storage is missing')
            conn.execute('SET LOCAL search_path=campaigniq_signedin_accounts_dev,pg_catalog')
            try:
                session=AccountDataSession(conn,access,False,gateway._directory.registry_id)
                session._physical_scope=guard.drafts.workspace
                session.artifacts=ReadOnlyArtifacts(session.artifacts)
                current=session.drafts.get(draft_id)
                if current.revision!=revision:raise ValueError('Draft changed; reload before viewing results')
                result=_results_session(service,session,current,account_id,revision)
                session._check()
                return result
            finally:
                if session is not None:session._active=False
