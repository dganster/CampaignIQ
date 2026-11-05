"""Owner monetary-basis approval, distinct from source receipts and publication."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256

from campaigniq import account_forex_financial_candidate as financial
from campaigniq import account_forex_daily_sources as daily
from campaigniq.forex_daily_financial_candidate import canonical, POLICY

ROLE = 'forex_financial_policy_approval_v1'
FILENAME = 'forex-financial-policy-approval.json'
FORMAT = 'campaigniq.owner_approved_forex_financial_policy'


def stable_candidate(body):
    value = deepcopy(body)
    value.pop('source_revision')
    return sha256(canonical(value).encode()).hexdigest()


def context(body):
    calculation = body['calculation']
    if (body['policy_id'] != POLICY or body['source_receipts_verified'] is not True or
        calculation['all_exact_financial_equations_verified'] is not True or
        calculation['authoritative_opening_matches'] is not True or
        calculation['all_original_financing_rows_classified'] is not True or
        calculation['ending_quantities'] or
        any(body[k] is not False for k in ('financial_policy_approved','financial_state_written','publication_authorized')) or
        any(calculation[k] is not False for k in ('policy_approved','financial_state_written','publication_authorized','original_discrepancies_resolved'))):
        raise ValueError('Complete classified daily-session financial candidate required')
    return dict(stable_candidate_sha256=stable_candidate(body),
        daily_source_receipt_sha256=body['daily_source_receipt_sha256'],
        cash_source_receipt_sha256=body['cash_source_receipt_sha256'],
        predecessor_artifact_sha256=body['predecessor_artifact_sha256'],
        binding_sha256=body['binding_sha256'],original_source_sha256=body['original_source_sha256'])


def make_receipt(session, draft, actor_id, reviewed):
    body = reviewed['artifact']
    return dict(format=FORMAT,version=1,policy_id=POLICY,account_id=session.account.account_id,
        draft_id=str(draft.id),year=draft.year,month=draft.month,
        confirmed_by=actor_id,confirmed_at=datetime.now(timezone.utc).isoformat(),
        confirmed_source_revision=draft.revision,reviewed_candidate_sha256=reviewed['artifact_sha256'],
        daily_reporting_session_basis_accepted=True,original_differences_retained=True,coverage_limits_accepted=True,
        financial_policy_approved=True,original_discrepancies_resolved=False,
        broker_verified_account_link=False,financial_state_written=False,publication_authorized=False,
        **context(body))


def load_receipt(session, draft):
    import json
    try: name,media,raw = session.drafts.document(draft.id,ROLE)
    except FileNotFoundError: return None
    if name!=FILENAME or media!='text/plain':raise ValueError('Invalid monetary-policy approval document')
    body=json.loads(raw,object_pairs_hook=daily.unique)
    fields={'format','version','policy_id','account_id','draft_id','year','month','confirmed_by','confirmed_at',
        'confirmed_source_revision','reviewed_candidate_sha256','daily_reporting_session_basis_accepted',
        'original_differences_retained','coverage_limits_accepted','financial_policy_approved',
        'original_discrepancies_resolved','broker_verified_account_link','financial_state_written','publication_authorized',
        'stable_candidate_sha256','daily_source_receipt_sha256','cash_source_receipt_sha256',
        'predecessor_artifact_sha256','binding_sha256','original_source_sha256'}
    if (not isinstance(body,dict) or set(body)!=fields or body['format']!=FORMAT or
        type(body['version']) is not int or body['version']!=1 or body['policy_id']!=POLICY or
        body['account_id']!=session.account.account_id or body['draft_id']!=str(draft.id) or
        type(body['year']) is not int or type(body['month']) is not int or
        (body['year'],body['month'])!=(2026,10) or (draft.year,draft.month)!=(2026,10) or
        type(body['confirmed_source_revision']) is not int or not 0<=body['confirmed_source_revision']<draft.revision or
        not isinstance(body['confirmed_by'],str) or not body['confirmed_by']):
        raise ValueError('Monetary-policy approval scope or protocol differs; no repair attempted')
    if (any(body[key] is not True for key in ('daily_reporting_session_basis_accepted','original_differences_retained',
        'coverage_limits_accepted','financial_policy_approved')) or
        any(body[key] is not False for key in ('original_discrepancies_resolved','broker_verified_account_link',
        'financial_state_written','publication_authorized'))):
        raise ValueError('Explicit monetary choices and separate publication authority required')
    if not isinstance(body['confirmed_at'],str) or datetime.fromisoformat(body['confirmed_at']).tzinfo is None:
        raise ValueError('Timezone-aware policy approval timestamp required')
    if not all(daily.is_hash(body[k]) for k in ('reviewed_candidate_sha256','stable_candidate_sha256',
        'daily_source_receipt_sha256','cash_source_receipt_sha256')):
        raise ValueError('Invalid monetary-policy approval fingerprints')
    for key, expected in (('binding_sha256',{'securities','forex'}),
        ('original_source_sha256',{'cash_ledger','trade_history','transaction_report','supplemental_history'})):
        if (not isinstance(body[key],dict) or set(body[key])!=expected or
            not all(daily.is_hash(v) for v in body[key].values())):
            raise ValueError('Invalid monetary-policy source or binding fingerprints')
    predecessor=body['predecessor_artifact_sha256']
    if not isinstance(predecessor,dict) or not predecessor or not all(daily.is_hash(v) for v in predecessor.values()):
        raise ValueError('Exact predecessor fingerprints required')
    return body


def saved_preview(service, session, draft):
    receipt = load_receipt(session,draft)
    if receipt is None:raise ValueError('Save the monetary-policy approval first')
    if receipt['confirmed_by']!=daily.current_owner(service,session):
        raise ValueError('Monetary-policy approval owner differs from current account owner')
    candidate=financial.preview(service,session,draft)
    if any(receipt[key]!=value for key,value in context(candidate['artifact']).items()):
        raise ValueError('Approved monetary calculation or sources changed; no adoption, overwrite or repair')
    # The reviewed full candidate hash includes the displayed revision. Rebuild
    # exactly that revision from the immutable calculation, rather than dropping
    # the reviewed hash when the approval upload increments the draft revision.
    reviewed=deepcopy(candidate['artifact'])
    reviewed['source_revision']=receipt['confirmed_source_revision']
    if sha256((canonical(reviewed)+'\n').encode()).hexdigest()!=receipt['reviewed_candidate_sha256']:
        raise ValueError('Reviewed monetary candidate fingerprint differs; no repair attempted')
    return dict(format='campaigniq.saved_forex_financial_policy_review',version=1,
        account_id=session.account.account_id,draft_id=str(draft.id),revision=draft.revision,
        approval_receipt_sha256=sha256(session.drafts.document(draft.id,ROLE)[2]).hexdigest(),
        confirmed_by=receipt['confirmed_by'],confirmed_at=receipt['confirmed_at'],
        financial_policy_approved=True,original_discrepancies_resolved=False,
        broker_verified_account_link=False,financial_state_written=False,publication_authorized=False,
        candidate=candidate,receipt=receipt,
        publication_blocker='Monetary basis approved; monthly writer/report integration and complete generation rehearsal remain required.')
