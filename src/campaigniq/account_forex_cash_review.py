"""Read-only account/draft-bound Forex cash source review; no save or writer."""
from calendar import monthrange
from datetime import date
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from campaigniq.broker_account_binding import verify_document
from campaigniq.forex_cash_ledger_preview import preview
from campaigniq.persistence.import_drafts import MAX_DOCUMENT_BYTES

HISTORY='thinkorswim_trade_history'
REPORT='schwab_forex_transaction_report'


def upload_bytes(upload):
    if not callable(getattr(upload,'getvalue',None)) or getattr(upload,'media_type',None) not in ('text/csv','text/plain','application/vnd.ms-excel'):
        raise ValueError('Original Forex cash CSV required')
    value=upload.getvalue()
    if not isinstance(value,bytes) or not 0<len(value)<=MAX_DOCUMENT_BYTES:
        raise ValueError('Original CSV at most 20 MiB required')
    value.decode('utf-8-sig')
    return value


def evaluate(service,session,draft,cash,supplement):
    """Use exact saved history/report and temporary uploaded candidates only."""
    if (draft.year,draft.month)!=(2026,10):
        raise ValueError('Forex cash review currently supports October 2026 only')
    _,history_media,history=session.drafts.document(draft.id,HISTORY)
    _,report_media,report=session.drafts.document(draft.id,REPORT)
    if history_media not in ('text/csv','text/plain') or report_media not in ('text/csv','text/plain'):
        raise ValueError('Saved original CSV sources required')
    known=service._known(session)
    for role,raw in ((HISTORY,history),(REPORT,report),(HISTORY,cash),(HISTORY,supplement)):
        verify_document(known,role,raw.decode('utf-8-sig'))
    with TemporaryDirectory(prefix='campaigniq-forex-cash-review-') as folder:
        paths={}
        for key,raw in (('cash_ledger',cash),('trade_history',history),('transaction_report',report),('supplemental_history',supplement)):
            paths[key]=Path(folder)/(key+'.csv');paths[key].write_bytes(raw)
        body=preview(**paths,period_end=date(draft.year,draft.month,monthrange(draft.year,draft.month)[1]))
    # This validates securities headers against one binding and the saved Forex
    # report against its separate binding. It is not independent proof linking
    # those two broker accounts or of the cash ledger's financial authority.
    return dict(format='campaigniq.account_forex_cash_source_review',version=1,
        account_id=session.account.account_id,draft_id=str(draft.id),revision=draft.revision,
        source_headers_match_bindings=True,broker_verified_cross_account_link=False,
        calculation=body,review_sha256=sha256(__import__('json').dumps(body,sort_keys=True,default=str).encode()).hexdigest(),
        source_documents_saved=False,financial_state_written=False,publication_authorized=False)


# Source-only receipts. These roles are not consumed by financial publication.
CASH='forex_cash_ledger_original_v1'
SUPPLEMENT='forex_supplemental_history_original_v1'
RECEIPT='forex_cash_source_choice_receipt_v1'
POLICY='forex_balanced_cash_candidate_october_2026_v1'
FORMAT='campaigniq.owner_confirmed_forex_cash_sources'


def canonical(value):
    import json
    return json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()


def fingerprint(value):return sha256(value).hexdigest()


def context(service,session,result):
    known=service._known(session)
    return dict(source_sha256=result['calculation']['source_sha256'],
        binding_sha256={kind:known[kind].digest for kind in ('securities','forex')},
        review_sha256=result['review_sha256'],policy_id=POLICY)


def make_receipt(session,draft,actor_id,ctx):
    from datetime import datetime,timezone
    return dict(format=FORMAT,version=1,account_id=session.account.account_id,draft_id=str(draft.id),
        year=draft.year,month=draft.month,confirmed_by=actor_id,confirmed_at=datetime.now(timezone.utc).isoformat(),
        confirmed_source_revision=draft.revision,source_choices_accepted=True,
        evidence_type='owner_confirmed_account_association_and_cash_source_choice',
        broker_verified_account_link=False,publication_authorized=False,**ctx)


def load_receipt(session,draft):
    import json,re
    from datetime import datetime
    try:_,media,raw=session.drafts.document(draft.id,RECEIPT)
    except FileNotFoundError:return None
    def unique(pairs):
        obj={}
        for k,v in pairs:
            if k in obj:raise ValueError('Duplicate Forex source receipt field')
            obj[k]=v
        return obj
    if media!='text/plain':raise ValueError('Invalid Forex receipt media type')
    r=json.loads(raw,object_pairs_hook=unique)
    fields={'format','version','account_id','draft_id','year','month','confirmed_by','confirmed_at',
        'confirmed_source_revision','source_choices_accepted','evidence_type','broker_verified_account_link',
        'publication_authorized','source_sha256','binding_sha256','review_sha256','policy_id'}
    if (not isinstance(r,dict) or set(r)!=fields or r['format']!=FORMAT or type(r['version']) is not int or r['version']!=1
        or r['account_id']!=session.account.account_id or r['draft_id']!=str(draft.id)
        or type(r['year']) is not int or type(r['month']) is not int or (r['year'],r['month'])!=(draft.year,draft.month)
        or r['policy_id']!=POLICY or r['source_choices_accepted'] is not True
        or r['evidence_type']!='owner_confirmed_account_association_and_cash_source_choice'
        or r['broker_verified_account_link'] is not False or r['publication_authorized'] is not False
        or type(r['confirmed_source_revision']) is not int or not 0<=r['confirmed_source_revision']<draft.revision
        or not isinstance(r['confirmed_by'],str) or not r['confirmed_by']):
        raise ValueError('Forex source receipt scope or policy differs; no repair attempted')
    def ishash(v):return isinstance(v,str) and re.fullmatch('[0-9a-f]{64}',v) is not None
    if (not isinstance(r['source_sha256'],dict) or set(r['source_sha256'])!={'cash_ledger','trade_history','transaction_report','supplemental_history'}
        or not isinstance(r['binding_sha256'],dict) or set(r['binding_sha256'])!={'securities','forex'}
        or not all(ishash(v) for v in (*r['source_sha256'].values(),*r['binding_sha256'].values(),r['review_sha256']))):
        raise ValueError('Invalid Forex source receipt fingerprints')
    if not isinstance(r['confirmed_at'],str) or datetime.fromisoformat(r['confirmed_at']).tzinfo is None:
        raise ValueError('Timezone-aware Forex confirmation timestamp required')
    return r


def originals(session,draft,receipt):
    docs={}
    for role,name in ((CASH,'cash_ledger'),(SUPPLEMENT,'supplemental_history')):
        try:_,media,raw=session.drafts.document(draft.id,role)
        except FileNotFoundError:
            if receipt is not None:raise ValueError('Confirmed Forex original is missing')
            continue
        if receipt is None or media!='text/csv' or fingerprint(raw)!=receipt['source_sha256'][name]:
            raise ValueError('Forex original has no matching valid receipt; no adoption or repair')
        docs[role]=raw
    return docs


def saved_preview(service,session,draft):
    receipt=load_receipt(session,draft)
    if receipt is None:raise ValueError('Save the Forex cash source confirmation first')
    docs=originals(session,draft,receipt)
    result=evaluate(service,session,draft,docs[CASH],docs[SUPPLEMENT])
    if any(receipt[k]!=v for k,v in context(service,session,result).items()):
        raise ValueError('Forex sources, binding or calculation changed; review current originals again')
    return dict(result,format='campaigniq.saved_forex_cash_source_review',source_receipt_verified=True,
        receipt_sha256=fingerprint(session.drafts.document(draft.id,RECEIPT)[2]),
        account_association_evidence='Owner confirmation, not an independent broker link',
        source_documents_saved=True,financial_state_written=False,publication_authorized=False)
