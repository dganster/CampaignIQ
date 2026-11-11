"""Account-scoped statement-only review and durable owner no-trading confirmation."""
from calendar import monthrange
from datetime import date,datetime,timezone
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from campaigniq.import_draft_bridge import restore_uploads
from campaigniq.import_contract import MonthlyInputRole
from campaigniq.monthly_upload_staging import stage_monthly_uploads
from campaigniq.no_trading_statement import analyze
from campaigniq.persistence.authoritative_lot_state import load_preceding_authoritative_state_from_storage
from campaigniq.persistence.lot_book_store import serialize_lot_book
from campaigniq.closing_inventory_reconciliation import reconcile_closing_inventory
from campaigniq.importers.schwab.position_snapshot_reader import read_position_snapshot_section
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.value_objects.forex_pair import ForexPair

ROLE='no_trading_owner_confirmation_v1'
FILENAME='no-trading-confirmation.json'
FORMAT='campaigniq.owner_confirmed_no_trading'
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True)
def fingerprint(v):return sha256(canonical(v).encode()).hexdigest()
def dates(draft):
    return date(draft.year,draft.month,1),date(draft.year,draft.month,monthrange(draft.year,draft.month)[1])
def receipt(session,draft):
    try:name,media,raw=session.drafts.document(draft.id,ROLE)
    except FileNotFoundError:return None
    from campaigniq.account_release_config import strict_json
    value=strict_json(raw)
    fields={'format','version','account_id','draft_id','year','month','confirmed_by','confirmed_at','confirmed_revision','review_sha256','no_trading_confirmed','cash_income_reviewed'}
    if (name!=FILENAME or media!='text/plain' or not isinstance(value,dict) or set(value)!=fields or
        value['format']!=FORMAT or type(value['version']) is not int or value['version']!=1 or
        (value['account_id'],value['draft_id'],value['year'],value['month'])!=(session.account.account_id,str(draft.id),draft.year,draft.month) or
        type(value['confirmed_revision']) is not int or not 0<=value['confirmed_revision']<draft.revision or
        value['no_trading_confirmed'] is not True or value['cash_income_reviewed'] is not True or
        not isinstance(value['confirmed_by'],str) or not value['confirmed_by'] or
        not isinstance(value['review_sha256'],str) or len(value['review_sha256'])!=64 or
        datetime.fromisoformat(value['confirmed_at']).tzinfo is None):
        raise ValueError('No-trading confirmation is corrupt or belongs to another source; no repair attempted')
    return value

def evaluate(service,session,draft):
    start,end=dates(draft);uploads=restore_uploads(session.drafts,draft.id)
    if 'schwab_brokerage_statement' not in uploads:raise ValueError('Upload the monthly brokerage statement first')
    optional_history=uploads.get('thinkorswim_trade_history')
    for role,u in uploads.items():
        if str(getattr(role,'value',role)) not in ('schwab_brokerage_statement','thinkorswim_trade_history') and u.content.strip():
            raise ValueError('No-trading mode requires the statement alone; supplied nonempty activity reports must use the normal workflow')
    for role in ('forex_cash_source_choice_receipt_v1','forex_daily_source_receipt_v1','forex_financial_policy_approval_v1',
                 'crypto_statement_sources_approval_v1','futures_empty_opening_confirmation_v1','futures_monthly_statement_pdf_v1',
                 'futures_statement_association_v1','futures_empty_closing_confirmation_v1'):
        try:session.drafts.document(draft.id,role)
        except FileNotFoundError:continue
        raise ValueError('Saved Forex, futures or crypto source choices require the normal monthly workflow')
    preceding=load_preceding_authoritative_state_from_storage(session.artifacts,period_start=start)
    if preceding is None or preceding[0]!=start-date.resolution:
        raise ValueError('Exact preceding published month required; no opening positions will be invented')
    previous,key,book=preceding
    for instrument,lots in book._lots.items():
        if any(l.quantity for l in lots) and isinstance(instrument,ForexPair):
            raise ValueError('Opening Forex positions require their own monthly controls')
        if any(l.quantity for l in lots) and isinstance(instrument,OptionContract) and instrument.expiration<=end:
            raise ValueError('An opening option expires by this month end; use the normal activity workflow')
    prevprefix=previous.strftime('%Y-%m')+'-'
    if session.artifacts.exists(prevprefix+'crypto.json'):
        crypto=json.loads(session.artifacts.read_text(prevprefix+'crypto.json'))
        from decimal import Decimal
        if any(Decimal(q) for q in crypto.get('ending_quantities',{}).values()):
            raise ValueError('Carried crypto holdings require their own monthly controls')
    if session.artifacts.exists(prevprefix+'futures.json'):
        raise ValueError('Prior futures evidence requires the normal monthly workflow')
    with TemporaryDirectory(prefix='campaigniq-no-trading-review-') as folder:
        root=Path(folder);statement_upload={'schwab_brokerage_statement':uploads['schwab_brokerage_statement']}
        supplied=stage_monthly_uploads(upload_dir=root,uploads=statement_upload)
        if any(not v.valid for v in service._identity_validations(session,statement_upload,supplied,draft=draft)):
            raise ValueError('Statement identity does not match the selected account')
        path=supplied[MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT];text=path.read_text()
        from campaigniq.import_validation import validate_monthly_input
        valid=validate_monthly_input(MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT,path,period_start=start,period_end=end)
        if not valid.valid:raise ValueError(valid.message)
        if 'Pending / Open Activity' in text:
            raise ValueError('Pending statement activity requires the normal monthly workflow')
        from campaigniq.importers.schwab.position_snapshot_reader import _EQUITY_RE,_OPTION_RE
        import re
        section=None
        for line in text.splitlines():
            line=line.strip()
            if line.startswith(('Positions - Equities','Positions - Exchange Traded Funds','Positions - Other Assets')):section='stock';continue
            if line.startswith('Positions - Options'):section='option';continue
            if line.startswith(('Total Equities','Total Exchange Traded Funds','Total Other Assets','Total Options')):section=None;continue
            if section and re.search(r'\(?-?[\d,]+\.\d{4}\)?\s+(?:S\s+)?[\d,]+\.\d{4,5}',line):
                parser=_OPTION_RE if section=='option' else _EQUITY_RE
                if parser.match(line) is None:raise ValueError('Unrecognized statement holding; no-trading confirmation withheld')
        cash=analyze(text,start=start,end=end)
        if optional_history is not None:
            from campaigniq.no_trading_export import check_history
            check_history(optional_history.content,service._known(session),start,end,cash)
        rows=read_position_snapshot_section(text.splitlines(),snapshot_at=datetime.combine(end,datetime.max.time()))
        control=reconcile_closing_inventory(ending_lot_book=book,snapshot_rows=rows,period_end=end)
        if not control.reconciled:raise ValueError('Unchanged opening positions do not match the monthly statement; no-trading confirmation withheld')
        from decimal import Decimal
        for row in rows:
            lots=book.lots(row.instrument())
            if any(not l.quantity.is_finite() or (l.basis_total is not None and not l.basis_total.is_finite()) for l in lots):
                raise ValueError('Finite canonical lot quantities and costs required')
            if row.basis_total is not None and all(l.basis_total is not None for l in lots):
                if sum((l.basis_total for l in lots),Decimal(0))!=row.basis_total:
                    raise ValueError('Statement cost basis differs from carried lots; review the normal monthly workflow')
    body=dict(format='campaigniq.no_trading_month_review',version=1,account_id=session.account.account_id,
        draft_id=str(draft.id),period_start=str(start),period_end=str(end),
        original_documents={str(getattr(r,'value',r)):dict(sha256=sha256(u.content).hexdigest(),byte_size=len(u.content)) for r,u in uploads.items()},
        predecessor_key=key,predecessor_sha256=sha256(session.artifacts.read_text(key).encode()).hexdigest(),
        opening_book_sha256=sha256(serialize_lot_book(period_end=previous,lot_book=book).encode()).hexdigest(),
        securities_binding_sha256=service._known(session)['securities'].digest,
        financials=cash,lot_count=sum(len(rows) for rows in book._lots.values()),inventory_reconciled=True)
    return body,book

def preview(service,session,draft,*,require_confirmation=False):
    prior=receipt(session,draft);body,book=evaluate(service,session,draft)
    if prior is not None:
        from campaigniq.account_forex_daily_sources import current_owner
        if prior['confirmed_by']!=current_owner(service,session) or prior['review_sha256']!=fingerprint(body):
            raise ValueError('No-trading confirmation no longer matches current owner, statement or preceding lots')
    if require_confirmation and prior is None:raise ValueError('Save the owner no-trading confirmation first')
    return dict(review=body,review_sha256=fingerprint(body),owner_confirmed=prior is not None,publication_authorized=False),book
