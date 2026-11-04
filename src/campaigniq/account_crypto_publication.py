"""Internal crypto generation writer; no public activation or connection creation."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from campaigniq.account_crypto_generation import build_candidate,verify_candidate
from campaigniq.account_crypto_statement_sources import HISTORY


def financial_report(service,session,draft,candidate):
    """Keep raw observations intact; approved financial choices are explicit."""
    from campaigniq.importers.thinkorswim.crypto_reader import read_crypto_report
    from campaigniq.persistence.crypto_month import build_crypto_month
    body=candidate.to_dict();calculation=body['calculation']
    start=date.fromisoformat(calculation['period_start']);end=date.fromisoformat(calculation['period_end'])
    with TemporaryDirectory(prefix='campaigniq-approved-crypto-report-') as temporary:
        path=Path(temporary)/'original.csv';path.write_bytes(session.drafts.document(draft.id,HISTORY)[2])
        raw=read_crypto_report(path,period_start=start,period_end=end)
    report=build_crypto_month(raw)
    report['warnings']=sorted(set(report['warnings']))
    report['snapshot_mismatches']=sorted(report['snapshot_mismatches'],key=lambda row:row['pair'])
    report['original_export_observations']=deepcopy(report)
    for key in ('opening_quantities','ending_quantities','ending_lots'):
        report[key]=deepcopy(calculation[key])
    report['opening_source']='approved preceding statement; acquisition costs documented separately'
    report['realized_sales']=deepcopy(calculation['realized_sales'])
    report['realized_pnl_usd']=calculation['totals']['proposed_realized_sales_pnl_usd']
    report['last_reported_cash_usd']=calculation['totals']['proposed_ending_cash_usd']
    report['status']='owner-approved statement sources; original export discrepancies retained'
    report['warnings']=list(dict.fromkeys([*report['warnings'],
        'Statement-based financial choices approved by owner; original source discrepancies remain unresolved.']))
    report['approved_statement_financials']=body
    # Raw cash/fill controls and month_end_reconciled are deliberately unchanged.
    return report


def reference(candidate,report_text):
    body=candidate.to_dict()
    return dict(format='campaigniq.approved_crypto_generation_reference',version=1,
        candidate_sha256=candidate.sha256,financial_record_sha256=sha256(report_text.encode()).hexdigest(),
        source_receipt_sha256=body['source_receipt_sha256'],source_revision=body['source_revision'])


def verify_published(service,session,draft):
    prefix=f'{draft.year:04d}-{draft.month:02d}-';key=prefix+'account-publication-receipt.json'
    if not session.artifacts.exists(key):return None
    receipt=json.loads(session.artifacts.read_text(key));descriptor=receipt.get('approved_crypto_generation')
    crypto_key=prefix+'crypto.json'
    report_text=session.artifacts.read_text(crypto_key) if session.artifacts.exists(crypto_key) else None
    report=json.loads(report_text) if report_text is not None else None
    embedded=report.get('approved_statement_financials') if isinstance(report,dict) else None
    if descriptor is None and embedded is None:
        from campaigniq.account_crypto_statement_sources import load_receipt
        if load_receipt(session,draft) is not None:raise ValueError('Approved crypto generation receipt is missing')
        return None
    if (not isinstance(descriptor,dict) or embedded is None or receipt.get('crypto_applicable') is not True or
        type(descriptor.get('version')) is not int or type(descriptor.get('source_revision')) is not int):
        raise ValueError('Complete approved crypto generation receipt required')
    from calendar import monthrange
    from campaigniq.persistence.monthly_publication import is_month_published_in_storage
    end=date(draft.year,draft.month,monthrange(draft.year,draft.month)[1])
    if not is_month_published_in_storage(session.artifacts,period_end=end):raise ValueError('Complete crypto monthly visibility marker required')
    candidate=verify_candidate(service,session,draft,json.dumps(embedded))
    expected=json.dumps(financial_report(service,session,draft,candidate),sort_keys=True,indent=2)+'\n'
    if report_text!=expected or descriptor!=reference(candidate,expected):
        raise ValueError('Approved crypto generation differs from verified originals')
    return candidate


def _publish_crypto_in_session(service,session,actor_id,account_id,draft_id,revision,*,forex_applicable,crypto_applicable):
    """Caller owns authorized serialized SQL transaction; marker is flushed last."""
    from campaigniq.account_futures_publication import GenerationBuffer
    from campaigniq.account_local_publication import _publish_in_session
    from campaigniq.futures_monthly_preview import has_futures
    if type(revision) is not int or any(type(v) is not bool for v in (forex_applicable,crypto_applicable)):
        raise ValueError('Displayed revision and explicit markets required')
    draft=service._lock(session,draft_id,write=True)
    if draft.revision!=revision:raise ValueError('Draft changed; reload before crypto publication')
    if not crypto_applicable:raise ValueError('Approved crypto cannot be omitted')
    if has_futures(session,draft):raise ValueError('Combined futures generation requires its separate verified writer')
    candidate=build_candidate(service,session,draft)
    target=session.artifacts;buffer=GenerationBuffer(target);prefix=f'{draft.year:04d}-{draft.month:02d}-'
    try:
        session.artifacts=buffer
        result=_publish_in_session(service,session,actor_id,account_id,draft_id,revision,
            forex_applicable=forex_applicable,crypto_applicable=True,
            receipt_format='campaigniq.deployment_account_publication',_crypto_candidate=candidate)
        key=prefix+'account-publication-receipt.json';receipt=json.loads(buffer.read_text(key))
        receipt['approved_crypto_generation']=reference(candidate,buffer.read_text(prefix+'crypto.json'))
        text=json.dumps(receipt,sort_keys=True,indent=2)+'\n';buffer.write_text(key,text)
        from campaigniq.account_deployment_results import _deployment_results_session
        _deployment_results_session(service,session,draft,account_id,revision)
        verify_published(service,session,draft);session._check()
        keys=tuple(sorted(buffer.rows));marker=prefix+'finalized.json'
        if marker not in keys:raise ValueError('Complete monthly visibility marker required')
    finally:session.artifacts=target
    for key in (*[k for k in keys if k!=marker],marker):target.write_text(key,buffer.rows[key])
    verify_published(service,session,draft);session._check()
    return replace(result,artifact_keys=target.list_keys(prefix=prefix),receipt_sha256=sha256(text.encode()).hexdigest())
