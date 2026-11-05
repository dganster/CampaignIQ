"""Atomic approved-Forex generation and independent source-bound result checks."""
from dataclasses import replace
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from campaigniq import account_forex_financial_policy as policy
from campaigniq.account_forex_generation import prepare,START,END
from campaigniq.forex_daily_financial_candidate import canonical
from campaigniq.persistence.approved_forex_month import SUFFIXES,reference,load_published
from campaigniq.persistence.forex_settlement_attribution_store import serialize_forex_settlement_attributions
from campaigniq.persistence.position_journal import serialize_position_journal
from campaigniq.persistence.lot_book_store import serialize_lot_book


def write_record(session,draft,context):
    prefix=f'{draft.year:04d}-{draft.month:02d}-'
    body=dict(format='campaigniq.approved_forex_month',version=1,policy_id=context.calculation['policy_id'],
        account_id=session.account.account_id,draft_id=str(draft.id),source_revision=draft.revision,
        period_start=str(START),period_end=str(END),approval_receipt_sha256=context.approved['approval_receipt_sha256'],
        approved_source_snapshot=context.approved,source_lineage=json.loads(context.lineage_json),
        financials=context.calculation['totals'],canonical_artifact_sha256={
            prefix+s:sha256(session.artifacts.read_text(prefix+s).encode()).hexdigest() for s in SUFFIXES})
    text=json.dumps(body,sort_keys=True,indent=2)+'\n'
    session.artifacts.write_text(prefix+'forex-approved-generation.json',text)
    return reference(body,text)


def _rebuild(service,session,draft):
    from campaigniq.import_draft_bridge import restore_uploads
    from campaigniq.monthly_upload_staging import stage_monthly_uploads
    from campaigniq.import_contract import MonthlyInputRole
    from campaigniq.import_preflight import prepare_monthly_import
    from campaigniq.monthly_import_execution import execute_monthly_import
    from campaigniq.account_reconciliation_preview import ReadOnlyArtifacts
    from campaigniq.futures_monthly_preview import has_futures,project_history
    from campaigniq.account_futures_preview import connected_preview
    uploads=restore_uploads(session.drafts,draft.id)
    with TemporaryDirectory(prefix='campaigniq-forex-generation-read-') as folder:
        root=Path(folder);supplied=stage_monthly_uploads(upload_dir=root,uploads=uploads)
        base=uploads['thinkorswim_trade_history'].content
        if has_futures(session,draft):
            path=root/'futures-projection.csv';project_history(base,connected_preview(service,session,draft),path)
            base=path.read_bytes()
        path=root/'forex-projection.csv';context=prepare(service,session,draft,base,path)
        supplied[MonthlyInputRole.THINKORSWIM_TRADE_HISTORY]=path
        storage=ReadOnlyArtifacts(session.artifacts)
        # Keep the published crypto choice for applicability checks. This replay
        # returns before crypto artifact construction and does not approve it.
        receipt=json.loads(session.artifacts.read_text(f'{draft.year:04d}-{draft.month:02d}-account-publication-receipt.json'))
        prepared=prepare_monthly_import(draft.year,draft.month,authoritative_state_root=root/'state',
            supplied_inputs=supplied,artifact_storage=storage,forex_applicable=True,crypto_applicable=receipt['crypto_applicable'])
        outcome=execute_monthly_import(prepared,authoritative_state_root=root/'state',supplied_inputs=supplied,
            artifact_storage=storage,preview_only=True,_approved_forex=context)
        if outcome.finalized or not outcome.closing_reconciliation.reconciled or outcome.closing_reconciliation.forex_source_findings:
            raise ValueError('Approved Forex independent generation replay does not reconcile')
        return context,{
            'forex-settlement-attributions.json':serialize_forex_settlement_attributions(period_start=START,period_end=END,attributions=outcome.result.forex_settlement_attributions),
            'position-journal.json':serialize_position_journal(outcome.result,period_start=START,period_end=END),
            'lot-book.json':serialize_lot_book(period_end=END,lot_book=outcome.result.ending_lot_book),
            'forex-source-resolution.json':json.dumps(outcome.forex_resolution,sort_keys=True,indent=2)+'\n'}


def verify_published(service,session,draft):
    start=date(draft.year,draft.month,1)
    from calendar import monthrange
    end=date(draft.year,draft.month,monthrange(draft.year,draft.month)[1])
    body=load_published(session.artifacts,period_start=start,period_end=end)
    if body is None:
        if policy.load_receipt(session,draft) is not None:raise ValueError('Approved Forex publication record is missing')
        return None
    context,expected=_rebuild(service,session,draft)
    if (body['approved_source_snapshot']!=context.approved or
        body['source_lineage']!=json.loads(context.lineage_json) or body['financials']!=context.calculation['totals']):
        raise ValueError('Published Forex differs from currently approved original evidence')
    prefix=start.strftime('%Y-%m')+'-'
    if any(session.artifacts.read_text(prefix+suffix)!=text for suffix,text in expected.items()):
        raise ValueError('Approved Forex money, journal or canonical lots differ from independent replay')
    return body


def publish_in_session(service,session,actor_id,account_id,draft_id,revision,*,forex_applicable,crypto_applicable):
    from campaigniq.account_futures_publication import GenerationBuffer
    from campaigniq.account_local_publication import _publish_in_session
    from campaigniq.account_deployment_results import _deployment_results_session
    target=session.artifacts;buffer=GenerationBuffer(target)
    try:
        session.artifacts=buffer
        result=_publish_in_session(service,session,actor_id,account_id,draft_id,revision,
            forex_applicable=forex_applicable,crypto_applicable=crypto_applicable,
            receipt_format='campaigniq.deployment_account_publication')
        draft=service._lock(session,draft_id)
        _deployment_results_session(service,session,draft,account_id,revision)
        keys=tuple(sorted(buffer.rows));marker=f'{draft.year:04d}-{draft.month:02d}-finalized.json'
        if marker not in keys:raise ValueError('Complete Forex visibility marker required')
        session._check()
    finally:session.artifacts=target
    for key in (*[k for k in keys if k!=marker],marker):target.write_text(key,buffer.rows[key])
    verify_published(service,session,draft);session._check()
    return replace(result,artifact_keys=target.list_keys(prefix=marker.removesuffix('finalized.json')))
