"""Complete unchanged-lot generation, atomically appended and independently rebuilt."""
from dataclasses import replace
from datetime import date
from hashlib import sha256
import json

from campaigniq import account_no_trading as mode
from campaigniq.import_pipeline import PeriodImportResult
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.boundary_reconstruction import BoundaryReconstruction
from campaigniq.persistence.lot_book_store import serialize_lot_book
from campaigniq.persistence.position_journal import serialize_position_journal
from campaigniq.persistence.realized_attribution_store import serialize_realized_attributions
from campaigniq.persistence.forex_settlement_attribution_store import serialize_forex_settlement_attributions
from campaigniq.persistence.lifecycle_transition_store import serialize_lifecycle_transitions
from campaigniq.persistence.boundary_completeness_store import serialize_boundary_completeness
from campaigniq.persistence.import_provenance import serialize_monthly_import_provenance,MonthlyInputProvenance
from campaigniq.import_contract import MonthlyInputRole

RECORD='no-trading-month.json'
def generation(session,draft,review,book):
    start,end=mode.dates(draft);prefix=start.strftime('%Y-%m')+'-'
    result=PeriodImportResult((),(),(),(),PositionHistory(),book.clone(),book.clone(),BoundaryReconstruction((),(),()),())
    values={
        'lot-book.json':serialize_lot_book(period_end=end,lot_book=book),
        'position-journal.json':serialize_position_journal(result,period_start=start,period_end=end),
        'realized-attributions.json':serialize_realized_attributions(period_start=start,period_end=end,attributions=()),
        'forex-settlement-attributions.json':serialize_forex_settlement_attributions(period_start=start,period_end=end,attributions=()),
        'lifecycle-transitions.json':serialize_lifecycle_transitions(period_start=start,period_end=end,transitions=()),
        'boundary-completeness.json':serialize_boundary_completeness(period_start=start,period_end=end,reconstruction=result.boundary_reconstruction),
        'market-applicability.json':json.dumps(dict(format='campaigniq.market_applicability',version=1,period_start=str(start),period_end=str(end),forex_applicable=False,crypto_applicable=False),sort_keys=True)}
    # Fingerprint actual original statement bytes. No invented empty broker CSV.
    source=review['original_documents']['schwab_brokerage_statement']
    provenance=tuple(MonthlyInputProvenance(role,source['sha256'],source['byte_size']) for role in
        (MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE,MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT))
    values['import-provenance.json']=serialize_monthly_import_provenance(period_start=start,period_end=end,inputs=provenance)
    rows={prefix+k:v for k,v in values.items()}
    raw=session.drafts.document(draft.id,mode.ROLE)[2]
    body=dict(format='campaigniq.no_trading_month',version=1,review=review,
        owner_confirmation_sha256=sha256(raw).hexdigest(),
        canonical_artifact_sha256={k:sha256(v.encode()).hexdigest() for k,v in rows.items()})
    rows[prefix+RECORD]=json.dumps(body,sort_keys=True,indent=2)+'\n'
    return rows,body

def verify(service,session,draft):
    start,end=mode.dates(draft);prefix=start.strftime('%Y-%m')+'-'
    confirmation=mode.receipt(session,draft)
    receipt=json.loads(session.artifacts.read_text(prefix+'account-publication-receipt.json'))
    descriptor=receipt.get('no_trading_generation')
    present=session.artifacts.exists(prefix+RECORD)
    if confirmation is None and descriptor is None and not present:return None
    if confirmation is None or descriptor is None or not present:raise ValueError('Complete no-trading publication evidence required')
    reviewed,book=mode.preview(service,session,draft,require_confirmation=True)
    expected,body=generation(session,draft,reviewed['review'],book)
    text=expected[prefix+RECORD]
    if (descriptor!=dict(sha256=sha256(text.encode()).hexdigest(),artifact_key=prefix+RECORD) or
        any(not session.artifacts.exists(k) or session.artifacts.read_text(k)!=v for k,v in expected.items())):
        raise ValueError('No-trading published income, lots or journal differ from independent original-source replay')
    return body

def publish(service,session,actor,account_id,draft_id,revision,*,forex_applicable,crypto_applicable):
    from campaigniq.account_local_publication import _periods,SyntheticPublication
    from campaigniq.historical_backfill_guard import assess_backfill
    from campaigniq.persistence.monthly_publication import ensure_publication_protocol_in_storage,publish_finalized_month_marker_to_storage,is_month_published_in_storage
    from campaigniq.account_futures_publication import GenerationBuffer
    from campaigniq.account_deployment_results import _deployment_results_session
    if forex_applicable or crypto_applicable:raise ValueError('This statement-only securities month requires Forex and Crypto marked not applicable')
    draft=service._lock(session,draft_id,write=True)
    if draft.revision!=revision:raise ValueError('Draft changed; reload before publication')
    start,end=mode.dates(draft);prefix=start.strftime('%Y-%m')+'-'
    periods=_periods(session.artifacts)
    if (not periods or periods[-1]!=start-date.resolution or assess_backfill(end,periods).blocked or
        not is_month_published_in_storage(session.artifacts,period_end=periods[-1]) or session.artifacts.list_keys(prefix=prefix)):
        raise ValueError('Exact next unpublished month required; existing artifacts are preserved')
    reviewed,book=mode.preview(service,session,draft,require_confirmation=True)
    rows,body=generation(session,draft,reviewed['review'],book)
    target=session.artifacts;buffer=GenerationBuffer(target)
    receipt=dict(format='campaigniq.deployment_account_publication',version=1,
        registry_id=service.gateway._directory.registry_id,account_id=account_id,actor_id=actor,draft_id=str(draft.id),
        draft_revision=revision,period_end=str(end),original_documents=reviewed['review']['original_documents'],
        forex_applicable=False,crypto_applicable=False,production_publication_authorized=False,
        no_trading_generation=dict(artifact_key=prefix+RECORD,sha256=sha256(rows[prefix+RECORD].encode()).hexdigest()))
    text=json.dumps(receipt,sort_keys=True,indent=2)+'\n'
    rows[prefix+'account-publication-receipt.json']=text
    try:
        session.artifacts=buffer
        ensure_publication_protocol_in_storage(buffer,first_period_end=end)
        for key,value in rows.items():buffer.write_text(key,value)
        publish_finalized_month_marker_to_storage(buffer,period_end=end)
        _deployment_results_session(service,session,draft,account_id,revision);session._check()
    finally:session.artifacts=target
    marker=prefix+'finalized.json'
    for key in (*sorted(k for k in buffer.rows if k!=marker),marker):target.write_text(key,buffer.rows[key])
    verify(service,session,draft);session._check()
    return SyntheticPublication(account_id,draft.year,draft.month,str(draft.id),revision,target.list_keys(prefix=prefix),sha256(text.encode()).hexdigest())
