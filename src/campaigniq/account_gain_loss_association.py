"""User-confirmed report association, distinct from broker-supplied identity.

Receipts live beside draft originals in the same SQL transaction. They grant no
access and never waive current bindings, period checks or reconciliation.
"""
from calendar import monthrange
from datetime import date,datetime,timezone
from hashlib import sha256
import json
import re
from campaigniq.broker_account_binding import document_identity,verify_document
from campaigniq.import_contract import MonthlyInputRole
from campaigniq.import_validation import validate_monthly_input

ROLE='schwab_realized_gain_loss'
RECEIPT_ROLE='gain_loss_account_confirmation_v1'
FORMAT='campaigniq.user_confirmed_gain_loss_account'

def digest(content):return sha256(content).hexdigest()

def check_sources(known,draft,report_path,statement_path):
    report=report_path.read_text(encoding='utf-8-sig')
    statement=statement_path.read_text(encoding='utf-8-sig')
    verify_document(known,'schwab_brokerage_statement',statement)
    start=date(draft.year,draft.month,1);end=date(draft.year,draft.month,monthrange(draft.year,draft.month)[1])
    for role,path in ((MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT,statement_path),(MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS,report_path)):
        result=validate_monthly_input(role,path,period_start=start,period_end=end)
        if not result.valid:raise ValueError(result.message)
    # Explicit identities always take precedence, including mismatches. Never
    # accept an association as a way around a full-number conflict.
    try:observed=document_identity(ROLE,report)
    except ValueError:
        if re.search(r'(?<![0-9])[0-9]{4}-?[0-9]{4}(?![0-9])',report):
            raise ValueError('Unrecognized full identifier in report; association is unavailable')
        # A visible masked suffix is additional evidence, never full identity.
        for suffix in re.findall(r'…([0-9]{3,4})\b',report):
            if not known['securities'].suffix.endswith(suffix):raise ValueError('Masked report suffix conflicts with the selected account')
    else:
        if observed!=known['securities']:raise ValueError('Document belongs to a different broker account')
    return known['securities'].digest

def make_receipt(session,draft,actor_id,known,report_path,statement_path,report_bytes,statement_bytes):
    binding=check_sources(known,draft,report_path,statement_path)
    return dict(format=FORMAT,version=1,account_id=session.account.account_id,
        draft_id=str(draft.id),year=draft.year,month=draft.month,
        confirmed_by=actor_id,confirmed_at=datetime.now(timezone.utc).isoformat(),
        binding_digest=binding,report_sha256=digest(report_bytes),statement_sha256=digest(statement_bytes))

def load_receipt(session,draft):
    try:_,media,content=session.drafts.document(draft.id,RECEIPT_ROLE)
    except FileNotFoundError:return None
    if media!='text/plain':raise ValueError('Invalid report confirmation media type')
    result=json.loads(content)
    fields={'format','version','account_id','draft_id','year','month','confirmed_by','confirmed_at','binding_digest','report_sha256','statement_sha256'}
    if (not isinstance(result,dict) or set(result)!=fields or result['format']!=FORMAT or type(result['version']) is not int or result['version']!=1
        or result['account_id']!=session.account.account_id or result['draft_id']!=str(draft.id)
        or type(result['year']) is not int or type(result['month']) is not int
        or (result['year'],result['month'])!=(draft.year,draft.month)
        or not isinstance(result['confirmed_by'],str) or not result['confirmed_by']):raise ValueError('Report confirmation scope differs')
    if any(not isinstance(result[k],str) or not re.fullmatch('[0-9a-f]{64}',result[k]) for k in ('binding_digest','report_sha256','statement_sha256')):raise ValueError('Invalid report confirmation fingerprint')
    when=datetime.fromisoformat(result['confirmed_at'])
    if when.tzinfo is None:raise ValueError('Report confirmation timestamp lacks timezone')
    return result

def validate_receipt(service,session,draft,uploads,supplied):
    receipt=load_receipt(session,draft)
    if receipt is None:raise ValueError('Confirm the Gain/Loss report belongs to this account and month')
    report=uploads[ROLE];statement=uploads['schwab_brokerage_statement']
    binding=check_sources(service._known(session),draft,service._path(ROLE,supplied),service._path('schwab_brokerage_statement',supplied))
    if (receipt['binding_digest']!=binding or receipt['report_sha256']!=digest(report.getvalue())
        or receipt['statement_sha256']!=digest(statement.getvalue())):raise ValueError('Report or statement changed; confirm their association again')
    return receipt
