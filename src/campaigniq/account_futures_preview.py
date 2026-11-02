"""Connect guarded account documents to an isolated read-only futures preview.

The owner confirms the statement's account association; a GMI identifier is not
silently treated as a securities or Forex account number. No publication API.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from campaigniq import account_futures_opening as opening
from campaigniq.futures_accounting import preview
from campaigniq.futures_source_audit import statement_controls
from campaigniq.pdf_text import extract_pdf_text

PDF_ROLE = 'futures_monthly_statement_pdf_v1'
RECEIPT_ROLE = 'futures_statement_association_v1'
FORMAT = 'campaigniq.user_associated_futures_statement'


def digest(content):return sha256(content).hexdigest()


def gmi_digest(text):
    values=re.findall(r'(?m)GMI ACCOUNT NUMBER:[ \t]*([^\r\n]+)',text)
    normalized={re.sub(r'[ \t]+','',value.strip()) for value in values}
    if len(normalized)!=1 or not all(re.fullmatch(r'[A-Z0-9]{6,24}',value) for value in normalized):
        raise ValueError('Futures statement GMI account identifier is missing, masked or ambiguous')
    return digest(('schwab:futures:gmi:'+next(iter(normalized))).encode())


def load_receipt(session,draft):
    try:_,media,content=session.drafts.document(draft.id,RECEIPT_ROLE)
    except FileNotFoundError:return None
    if media!='text/plain':raise ValueError('Invalid futures statement association media type')
    record=json.loads(content,object_pairs_hook=opening._unique_object)
    fields={'format','version','account_id','draft_id','year','month','confirmed_by','confirmed_at',
            'confirmed_source_revision','evidence_type','broker_verified_account_link',
            'history_sha256','binding_digest','opening_confirmation_sha256','statement_sha256',
            'gmi_account_digest','statement_date'}
    if (not isinstance(record,dict) or set(record)!=fields or record['format']!=FORMAT or
            type(record['version']) is not int or record['version']!=1 or
            record['account_id']!=session.account.account_id or record['draft_id']!=str(draft.id) or
            type(record['year']) is not int or type(record['month']) is not int or
            (record['year'],record['month'])!=(draft.year,draft.month) or
            record['evidence_type']!='user_confirmed_account_association' or
            record['broker_verified_account_link'] is not False or
            type(record['confirmed_source_revision']) is not int or
            not 0<=record['confirmed_source_revision']<draft.revision or
            not isinstance(record['confirmed_by'],str) or not record['confirmed_by']):
        raise ValueError('Futures statement association scope or evidence type differs')
    for key in ('history_sha256','binding_digest','opening_confirmation_sha256','statement_sha256','gmi_account_digest'):
        if not isinstance(record[key],str) or not re.fullmatch(r'[0-9a-f]{64}',record[key]):
            raise ValueError('Invalid futures statement association fingerprint')
    if not isinstance(record['confirmed_at'],str) or datetime.fromisoformat(record['confirmed_at']).tzinfo is None:
        raise ValueError('Futures statement association requires a server timestamp with timezone')
    if not isinstance(record['statement_date'],str):raise ValueError('Invalid futures statement date')
    when=datetime.strptime(record['statement_date'],'%Y-%m-%d')
    if (when.year,when.month)!=(draft.year,draft.month):raise ValueError('Futures statement month differs')
    return record


def evaluate(service,session,draft,pdf_bytes):
    confirmed=opening.validate_receipt(service,session,draft)
    _,_,receipt_bytes=session.drafts.document(draft.id,opening.ROLE)
    _,_,history=session.drafts.document(draft.id,opening.HISTORY)
    if not pdf_bytes.startswith(b'%PDF-'):raise ValueError('A futures monthly statement PDF is required')
    with TemporaryDirectory(prefix='campaigniq-account-futures-') as temporary:
        root=Path(temporary)
        history_path=root/'history.csv';history_path.write_bytes(history)
        pdf_path=root/'futures-statement.pdf';pdf_path.write_bytes(pdf_bytes)
        text=extract_pdf_text(pdf_path,root/'statement.txt').read_text(encoding='utf-8')
        account=gmi_digest(text)
        result=preview(history_path,text,year=draft.year,month=draft.month)
    context=dict(history_sha256=confirmed['history_sha256'],binding_digest=confirmed['binding_digest'],
        opening_confirmation_sha256=digest(receipt_bytes),statement_sha256=digest(pdf_bytes),
        gmi_account_digest=account,
        statement_date=statement_controls(text)['statement_date'])
    return context,result


def make_receipt(session,draft,actor_id,context):
    return dict(format=FORMAT,version=1,account_id=session.account.account_id,draft_id=str(draft.id),
        year=draft.year,month=draft.month,confirmed_by=actor_id,
        confirmed_at=datetime.now(timezone.utc).isoformat(),confirmed_source_revision=draft.revision,
        evidence_type='user_confirmed_account_association',broker_verified_account_link=False,**context)


def existing_pdf(session,draft,receipt):
    try:_,media,content=session.drafts.document(draft.id,PDF_ROLE)
    except FileNotFoundError:
        if receipt is not None:raise ValueError('Associated futures statement original is missing')
        return None
    if receipt is None or media!='application/pdf' or digest(content)!=receipt['statement_sha256']:
        raise ValueError('Futures statement original and association differ; no repair attempted')
    return content


def connected_preview(service,session,draft):
    receipt=load_receipt(session,draft)
    content=existing_pdf(session,draft,receipt)
    if receipt is None or content is None:raise ValueError('Save and confirm the futures monthly statement first')
    context,result=evaluate(service,session,draft,content)
    if any(receipt[key]!=value for key,value in context.items()):
        raise ValueError('Futures statement, opening confirmation or source changed; confirm the statement again')
    result.update(account_id=session.account.account_id,draft_id=str(draft.id),source_revision=draft.revision,
        opening_positions_confirmed_by_owner=True,
        opening_state_assumption='Empty opening ES book confirmed by account owner; not independently broker-verified',
        opening_evidence_type='user_confirmation',statement_account_association='user_confirmed',
        statement_account_link_broker_verified=False,statement_sha256=context['statement_sha256'])
    result['blockers']=['Ending positions are calculated, not independently verified.',
                        'Futures publication integration remains pending.',
                        'Crypto remains separate and requires its own statement.']
    return result
