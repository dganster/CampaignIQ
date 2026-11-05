"""Append-only daily PDF originals and owner-attributed review receipts."""
from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re

from campaigniq import account_forex_cash_review as cash
from campaigniq import account_forex_daily_inventory as daily

PREFIX = 'forex_daily_pdf_original_v1:'
RECEIPT = 'forex_daily_source_receipt_v1'
FORMAT = 'campaigniq.owner_confirmed_forex_daily_sources'
POLICY = 'october_daily_inventory_sources_v1'


def digest(raw): return sha256(raw).hexdigest()


def stable_review(result):
    body = deepcopy(result)
    body.pop('revision', None)
    body.pop('review_sha256', None)
    body['original_cash_review'].pop('revision', None)
    return digest(cash.canonical(body))


def inventory(result, pdfs):
    by_hash = {digest(raw): (name, raw) for name, raw in pdfs}
    if len(by_hash) != len(pdfs):
        raise ValueError('Duplicate daily original PDF')
    output = []
    for check in result['daily_checks']:
        day = check['period_end']
        name, raw = by_hash[check['pdf_sha256']]
        output.append(dict(date=day, role=PREFIX+day, original_archive_path=name,
            stored_filename='forex-daily-'+day+'.pdf', pdf_sha256=digest(raw)))
    return output


def context(service, session, result, pdfs):
    known = service._known(session)
    return dict(archive_sha256=result['archive_sha256'], pdf_inventory=inventory(result, pdfs),
        source_sha256=result['original_cash_review']['calculation']['source_sha256'],
        cash_source_receipt_sha256=result['original_cash_review']['receipt_sha256'],
        binding_sha256={kind: known[kind].digest for kind in ('securities', 'forex')},
        predecessor_artifact_sha256=result['predecessor_artifact_sha256'],
        calculation_sha256=stable_review(result),
        coverage={key: result[key] for key in ('last_independent_daily_boundary', 'full_independent_daily_coverage',
            'dates_without_supplied_daily_reports', 'ledger_tail_no_activity_recorded', 'combined_inventory_evidence_reconciled')})


def make_receipt(session, draft, actor_id, ctx):
    return dict(format=FORMAT, version=1, policy=POLICY, account_id=session.account.account_id,
        draft_id=str(draft.id), year=draft.year, month=draft.month, confirmed_by=actor_id,
        confirmed_at=datetime.now(timezone.utc).isoformat(), confirmed_source_revision=draft.revision,
        same_forex_account_confirmed=True, coverage_limits_accepted=True,
        evidence_type='owner_account_association_and_daily_inventory_review',
        broker_verified_account_link=False, publication_authorized=False, archive_bytes_saved=False, **ctx)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('Duplicate daily source receipt field')
        result[key] = value
    return result


def is_hash(value):return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None


def load_receipt(session, draft):
    try: _, media, raw = session.drafts.document(draft.id, RECEIPT)
    except FileNotFoundError: return None
    if media != 'text/plain': raise ValueError('Invalid daily receipt media type')
    body = json.loads(raw, object_pairs_hook=unique)
    fields = {'format','version','policy','account_id','draft_id','year','month','confirmed_by','confirmed_at',
        'confirmed_source_revision','same_forex_account_confirmed','coverage_limits_accepted','evidence_type',
        'broker_verified_account_link','publication_authorized','archive_bytes_saved','archive_sha256','pdf_inventory',
        'source_sha256','cash_source_receipt_sha256','binding_sha256','predecessor_artifact_sha256','calculation_sha256','coverage'}
    if (not isinstance(body,dict) or set(body)!=fields or body['format']!=FORMAT or body['policy']!=POLICY
        or type(body['version']) is not int or body['version']!=1
        or body['account_id']!=session.account.account_id or body['draft_id']!=str(draft.id)
        or type(body['year']) is not int or type(body['month']) is not int
        or (body['year'],body['month'])!=(draft.year,draft.month) or (draft.year,draft.month)!=(2026,10)
        or not isinstance(body['confirmed_by'],str) or not body['confirmed_by']
        or type(body['confirmed_source_revision']) is not int or not 0<=body['confirmed_source_revision']<draft.revision
        or body['same_forex_account_confirmed'] is not True or body['coverage_limits_accepted'] is not True
        or body['evidence_type']!='owner_account_association_and_daily_inventory_review'
        or body['broker_verified_account_link'] is not False or body['publication_authorized'] is not False
        or body['archive_bytes_saved'] is not False):
        raise ValueError('Daily source receipt scope, policy or evidence type differs; no repair attempted')
    if not isinstance(body['confirmed_at'],str) or datetime.fromisoformat(body['confirmed_at']).tzinfo is None:
        raise ValueError('Daily source receipt requires a timezone-aware server timestamp')
    for key in ('archive_sha256','cash_source_receipt_sha256','calculation_sha256'):
        if not is_hash(body[key]): raise ValueError('Invalid daily receipt fingerprint')
    for key, keys in (('source_sha256',{'cash_ledger','trade_history','transaction_report','supplemental_history'}),
                       ('binding_sha256',{'securities','forex'})):
        if not isinstance(body[key],dict) or set(body[key])!=keys or not all(is_hash(v) for v in body[key].values()):
            raise ValueError('Invalid daily source/binding fingerprints')
    hashes = body['predecessor_artifact_sha256']
    allowed = {'.campaigniq-monthly-publication-v1.json','2026-09-finalized.json','2026-09-lot-book.json'}
    if not isinstance(hashes,dict) or not set(hashes)<=allowed or not all(is_hash(v) for v in hashes.values()):
        raise ValueError('Invalid predecessor receipt fingerprints')
    entries = body['pdf_inventory']
    if not isinstance(entries,list) or not 1<=len(entries)<=31:
        raise ValueError('Invalid daily PDF receipt inventory')
    dates, pdf_hashes = [], []
    for item in entries:
        if not isinstance(item,dict) or set(item)!={'date','role','original_archive_path','stored_filename','pdf_sha256'}:
            raise ValueError('Invalid daily PDF inventory fields')
        if not isinstance(item['date'],str): raise ValueError('Invalid daily inventory date')
        day = date.fromisoformat(item['date'])
        name = item['original_archive_path']
        if (day.year,day.month)!=(2026,10) or day.weekday()>=5 or item['role']!=PREFIX+str(day) or item['stored_filename']!='forex-daily-'+str(day)+'.pdf':
            raise ValueError('Invalid daily PDF inventory scope')
        if (not isinstance(name,str) or not name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
            or '\\' in name or PurePosixPath(name).suffix.lower()!='.pdf' or not is_hash(item['pdf_sha256'])):
            raise ValueError('Invalid daily PDF original identity')
        dates.append(item['date']); pdf_hashes.append(item['pdf_sha256'])
    if dates!=sorted(set(dates)) or len(set(pdf_hashes))!=len(pdf_hashes):
        raise ValueError('Duplicate or unordered daily PDF inventory')
    coverage = body['coverage']
    if (not isinstance(coverage,dict) or set(coverage)!={'last_independent_daily_boundary','full_independent_daily_coverage',
        'dates_without_supplied_daily_reports','ledger_tail_no_activity_recorded','combined_inventory_evidence_reconciled'}
        or coverage['last_independent_daily_boundary']!=dates[-1]
        or any(type(coverage[key]) is not bool for key in ('full_independent_daily_coverage','ledger_tail_no_activity_recorded','combined_inventory_evidence_reconciled'))
        or not isinstance(coverage['dates_without_supplied_daily_reports'],list)):
        raise ValueError('Invalid daily receipt coverage')
    from datetime import timedelta
    expected_dates = []
    cursor = date.fromisoformat(dates[-1])+timedelta(days=1)
    while cursor <= date(2026,10,30):
        if cursor.weekday()<5: expected_dates.append(str(cursor))
        cursor += timedelta(days=1)
    if coverage['dates_without_supplied_daily_reports']!=expected_dates or coverage['full_independent_daily_coverage'] is not (not expected_dates):
        raise ValueError('Daily receipt coverage dates differ')
    return body


def current_roles(session, draft):
    with session._operation() as conn:
        rows = conn.execute('''SELECT d.role FROM import_draft_documents d JOIN import_drafts p ON p.id=d.draft_id
            WHERE p.id=%s AND p.workspace=%s AND NOT d.superseded AND left(d.role,%s)=%s ORDER BY d.role''',
            (draft.id, session.drafts.workspace, len(PREFIX), PREFIX)).fetchall()
    return {row[0] for row in rows}


def saved_originals(session, draft, receipt):
    roles = current_roles(session, draft)
    expected = {item['role'] for item in receipt['pdf_inventory']} if receipt else set()
    if roles != expected:
        raise ValueError('Daily originals are missing or have no matching receipt; no adoption or repair')
    pdfs = []
    if receipt:
        for item in receipt['pdf_inventory']:
            filename, media, raw = session.drafts.document(draft.id, item['role'])
            if filename!=item['stored_filename'] or media!='application/pdf' or digest(raw)!=item['pdf_sha256']:
                raise ValueError('Saved daily PDF differs from owner receipt; no repair attempted')
            pdfs.append((item['original_archive_path'], raw))
    return pdfs


def current_owner(service, session):
    from campaigniq.persistence.deployment_account_directory import DirectoryTransaction
    directory = DirectoryTransaction(session._connection, service.gateway._directory.settings)._load()
    return next(account.owner_id for account in directory.accounts if account.id == session.account.account_id)


def saved_preview(service, session, draft):
    receipt = load_receipt(session, draft)
    if receipt is None:
        saved_originals(session, draft, None)
        raise ValueError('Save the daily Forex source confirmation first')
    if receipt['confirmed_by'] != current_owner(service, session):
        raise ValueError('Daily source receipt owner differs from the account owner')
    pdfs = saved_originals(session, draft, receipt)
    result = daily.evaluate_originals(service, session, draft, pdfs, archive_sha256=receipt['archive_sha256'])
    if any(receipt[key]!=value for key,value in context(service, session, result, pdfs).items()):
        raise ValueError('Daily sources, bindings, predecessor or calculation changed; review current originals again')
    result.update(format='campaigniq.saved_forex_daily_source_review', daily_pdfs_saved=True,
        source_receipt_verified=True, daily_source_receipt_sha256=digest(session.drafts.document(draft.id,RECEIPT)[2]),
        archive_bytes_saved=False, confirmed_by=receipt['confirmed_by'], confirmed_at=receipt['confirmed_at'])
    result['blockers'][0]='Daily originals and owner receipt verified; financial source policy and publication integration remain pending.'
    return result
