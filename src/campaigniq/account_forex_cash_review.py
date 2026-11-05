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
