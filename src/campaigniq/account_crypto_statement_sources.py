"""Account-scoped exact crypto PDFs and source-choice receipt; no financial writer."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import re

from campaigniq.broker_account_binding import verify_document
from campaigniq.crypto_statement_policy_preview import POLICY,preview_files
from campaigniq.importers.thinkorswim.crypto_reader import read_crypto_report
from campaigniq.persistence.import_drafts import MAX_DOCUMENT_BYTES

HISTORY='thinkorswim_trade_history'
PREVIOUS='crypto_previous_statement_pdf_v1'
CURRENT='schwab_crypto_statement'
RECEIPT='crypto_statement_sources_approval_v1'
FORMAT='campaigniq.owner_approved_crypto_statement_sources'


def digest(content):return sha256(content).hexdigest()
def _hash(value):return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None


def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate crypto source receipt field')
        result[key]=value
    return result


def upload_bytes(upload):
    if getattr(upload,'media_type',None)!='application/pdf' or not callable(getattr(upload,'getvalue',None)):
        raise ValueError('Original crypto statement PDF required')
    content=upload.getvalue()
    if (upload.media_type!='application/pdf' or not isinstance(content,bytes) or
        not content.startswith(b'%PDF-') or not 0<len(content)<=MAX_DOCUMENT_BYTES):
        raise ValueError('Original crypto statement PDF at most 20 MiB required')
    return content


def evaluate(service,session,draft,previous,current):
    _,_,history=session.drafts.document(draft.id,HISTORY)
    known=service._known(session)
    verify_document(known,HISTORY,history.decode('utf-8-sig'))
    with TemporaryDirectory(prefix='campaigniq-account-crypto-sources-') as temporary:
        root=Path(temporary);paths={}
        for name,content in (('history.csv',history),('previous.pdf',previous),('current.pdf',current)):
            paths[name]=root/name;paths[name].write_bytes(content)
        result=preview_files(paths['history.csv'],paths['previous.pdf'],paths['current.pdf'],year=draft.year,month=draft.month)
        body=result.to_dict()
        from datetime import date
        report=read_crypto_report(paths['history.csv'],period_start=date.fromisoformat(body['period_start']),period_end=date.fromisoformat(body['period_end']))
        number=report['account'] if report is not None else None
        if not isinstance(number,str) or re.fullmatch('Crypto #[A-Z0-9]{6,32}',number) is None:
            raise ValueError('A full crypto account identifier in the bound Trade History is required')
    context=dict(source_sha256=body['source_sha256'],binding_digest=known['securities'].digest,
        crypto_account_digest=digest(('schwab:crypto:'+number).encode()),account_suffix=body['account_suffix'],
        policy_id=POLICY,preview_sha256=result.sha256,review_sha256=body['review_sha256'])
    return context,body


def load_receipt(session,draft):
    try:_,media,raw=session.drafts.document(draft.id,RECEIPT)
    except FileNotFoundError:return None
    if media!='text/plain':raise ValueError('Invalid crypto source receipt media type')
    r=json.loads(raw,object_pairs_hook=_unique_object)
    fields={'format','version','account_id','draft_id','year','month','confirmed_by','confirmed_at','confirmed_source_revision',
        'evidence_type','broker_verified_account_link','source_choices_accepted','source_sha256','binding_digest',
        'crypto_account_digest','account_suffix','policy_id','preview_sha256','review_sha256'}
    if (not isinstance(r,dict) or set(r)!=fields or r['format']!=FORMAT or type(r['version']) is not int or r['version']!=1 or
        r['account_id']!=session.account.account_id or r['draft_id']!=str(draft.id) or
        type(r['year']) is not int or type(r['month']) is not int or (r['year'],r['month'])!=(draft.year,draft.month) or
        r['evidence_type']!='owner_confirmed_crypto_account_and_source_choices' or r['broker_verified_account_link'] is not False or
        r['source_choices_accepted'] is not True or r['policy_id']!=POLICY or
        type(r['confirmed_source_revision']) is not int or not 0<=r['confirmed_source_revision']<draft.revision or
        not isinstance(r['confirmed_by'],str) or not r['confirmed_by'] or
        not isinstance(r['account_suffix'],str) or re.fullmatch('[0-9]{4}',r['account_suffix']) is None):
        raise ValueError('Crypto source receipt scope or policy differs; no repair attempted')
    if (not isinstance(r['source_sha256'],dict) or set(r['source_sha256'])!={'trade_history','previous_crypto_statement','current_crypto_statement'} or
        not all(_hash(v) for v in r['source_sha256'].values()) or
        not all(_hash(r[k]) for k in ('binding_digest','crypto_account_digest','preview_sha256','review_sha256'))):
        raise ValueError('Invalid crypto source receipt fingerprints')
    if not isinstance(r['confirmed_at'],str) or datetime.fromisoformat(r['confirmed_at']).tzinfo is None:
        raise ValueError('Server confirmation timestamp with timezone required')
    return r


def originals(session,draft,receipt):
    result={}
    for role,name in ((PREVIOUS,'previous_crypto_statement'),(CURRENT,'current_crypto_statement')):
        try:_,media,content=session.drafts.document(draft.id,role)
        except FileNotFoundError:
            if receipt is not None:raise ValueError('Approved crypto statement original is missing')
            continue
        if receipt is None:
            # The current statement is an existing ordinary upload role. An owner
            # may confirm a previously saved, unapproved input after fresh review.
            # The previous-only role is created atomically with this receipt.
            if role!=CURRENT or media!='application/pdf' or not content.startswith(b'%PDF-'):
                raise ValueError('Unapproved crypto source metadata is inconsistent')
            result[role]=content
            continue
        if media!='application/pdf' or digest(content)!=receipt['source_sha256'][name]:
            raise ValueError('Crypto statement original differs from its receipt; no adoption or repair')
        result[role]=content
    return result


def saved_preview(service,session,draft):
    receipt=load_receipt(session,draft)
    if receipt is None:raise ValueError('Save the account-scoped crypto source confirmation first')
    docs=originals(session,draft,receipt)
    context,body=evaluate(service,session,draft,docs[PREVIOUS],docs[CURRENT])
    if any(receipt[key]!=value for key,value in context.items()):
        raise ValueError('Crypto source, binding or calculation changed; review the current documents again')
    return dict(format='campaigniq.account_crypto_statement_preview',version=1,
        account_id=session.account.account_id,draft_id=str(draft.id),revision=draft.revision,
        source_receipt_verified=True,policy_id=receipt['policy_id'],
        account_association_evidence='owner confirmation; not a broker-verified full-account link',
        receipt_sha256=digest(session.drafts.document(draft.id,RECEIPT)[2]),
        calculation=body,financial_state_written=False,publication_authorized=False)


def make_receipt(session,draft,actor_id,context):
    return dict(format=FORMAT,version=1,account_id=session.account.account_id,draft_id=str(draft.id),
        year=draft.year,month=draft.month,confirmed_by=actor_id,confirmed_at=datetime.now(timezone.utc).isoformat(),
        confirmed_source_revision=draft.revision,evidence_type='owner_confirmed_crypto_account_and_source_choices',
        broker_verified_account_link=False,source_choices_accepted=True,**context)
