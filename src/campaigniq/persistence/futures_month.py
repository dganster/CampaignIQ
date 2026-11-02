"""Canonical flat-book ES financial record and strict, read-only report loading."""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date,datetime
from decimal import Decimal,InvalidOperation
from hashlib import sha256
import json
import re

from campaigniq.futures_accounting import ESFuture,ESOption,OptionFill,Delivery,replay
from campaigniq.persistence.monthly_publication import is_month_published_in_storage,finalized_month_marker_key,publication_cutover_from_storage

FORMAT='campaigniq.futures_month'
SOURCE_ROLES={'thinkorswim_trade_history','futures_monthly_statement_pdf_v1',
    'futures_empty_opening_confirmation_v1','futures_statement_association_v1','futures_empty_closing_confirmation_v1'}


def _object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate futures record field')
        result[key]=value
    return result


def _number(value):
    if not isinstance(value,str):raise ValueError('Futures numbers must be exact decimal strings')
    try:result=Decimal(value)
    except InvalidOperation as error:raise ValueError('Invalid futures decimal') from error
    if not result.is_finite():raise ValueError('Nonfinite futures amount')
    return result


def _hash(value):
    return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None


@dataclass(frozen=True)
class FuturesMonth:
    account_id:str
    draft_id:str
    period_start:date
    period_end:date
    source_revision:int
    option_realized_usd:Decimal
    futures_realized_usd:Decimal
    fees_cash_usd:Decimal
    net_realized_usd:Decimal
    cash_sweeps_usd:Decimal
    record_json:str

    def to_dict(self):return json.loads(self.record_json)


def parse_record(text,*,account_id,draft_id,period_start,period_end):
    r=json.loads(text,object_pairs_hook=_object)
    fields={'format','version','account_id','draft_id','period_start','period_end','source_revision',
        'currency','multiplier_usd','execution_time_basis','evidence','source_refs','source_lineage',
        'financials','fills','deliveries','ending_options','ending_futures'}
    if (not isinstance(r,dict) or set(r)!=fields or r['format']!=FORMAT or type(r['version']) is not int or r['version']!=1 or
        r['account_id']!=account_id or r['draft_id']!=draft_id or not isinstance(account_id,str) or not isinstance(draft_id,str) or not account_id or not draft_id or
        r['period_start']!=period_start.isoformat() or r['period_end']!=period_end.isoformat() or
        period_start.day!=1 or period_end!=date(period_start.year,period_start.month,monthrange(period_start.year,period_start.month)[1]) or
        type(r['source_revision']) is not int or r['source_revision']<0 or r['currency']!='USD' or r['multiplier_usd']!='50' or
        r['execution_time_basis']!='broker_export_local_time' or r['ending_options']!={} or r['ending_futures']!={}):
        raise ValueError('Invalid futures record scope, profile or ending state')
    if r['evidence']!=dict(opening='owner_confirmed_empty',ending='owner_confirmed_empty',
        statement_account_association='owner_confirmed',independent_broker_position_verification=False):
        raise ValueError('Futures evidence type differs')
    if r['evidence']['independent_broker_position_verification'] is not False:raise ValueError('Invalid broker evidence flag')
    if not isinstance(r['source_refs'],dict) or set(r['source_refs'])!=SOURCE_ROLES or not all(_hash(v) for v in r['source_refs'].values()):
        raise ValueError('Invalid futures source fingerprints')
    lineage=r['source_lineage']
    if (not isinstance(lineage,dict) or set(lineage)!={'format','version','original_sha256','derived_sha256','removed_futures_row_ids','original_preserved'} or
        lineage['format']!='campaigniq.derived_monthly_history_lineage' or type(lineage['version']) is not int or lineage['version']!=1 or
        lineage['original_preserved'] is not True or lineage['original_sha256']!=r['source_refs']['thinkorswim_trade_history'] or
        not _hash(lineage['derived_sha256'])):raise ValueError('Invalid futures derived-source lineage')
    financials=r['financials']
    if not isinstance(financials,dict) or set(financials)!={'option_premium_cash_usd','option_realized_usd',
        'futures_realized_usd','fees_cash_usd','net_realized_usd','cash_sweeps_usd'}:
        raise ValueError('Invalid futures financial fields')
    amounts={k:_number(v) for k,v in financials.items()}
    if any(v!=v.quantize(Decimal('.01')) for v in amounts.values()) or amounts['fees_cash_usd']>0:
        raise ValueError('Unsupported futures money precision or fee credit')
    if amounts['net_realized_usd']!=amounts['option_realized_usd']+amounts['futures_realized_usd']+amounts['fees_cash_usd']:
        raise ValueError('Futures financial components do not add to net result')
    if not isinstance(r['fills'],list) or not r['fills'] or not isinstance(r['deliveries'],list):raise ValueError('Missing futures journal')
    fills=[];deliveries=[]
    for row in r['fills']:
        if not isinstance(row,dict) or set(row)!={'source','order','executed_at','underlying','series','expiration','strike','option_type','quantity','effect','price'}:
            raise ValueError('Invalid futures execution journal fields')
        if not isinstance(row['source'],str) or not re.fullmatch('history-row-[0-9]+',row['source']) or not re.fullmatch('history-order-[0-9]+',row['order']):
            raise ValueError('Invalid futures execution source identity')
        when=datetime.fromisoformat(row['executed_at'])
        if when.tzinfo is not None or not period_start<=when.date()<=period_end:raise ValueError('Invalid futures execution date')
        contract=ESOption(ESFuture(row['underlying']),row['series'],date.fromisoformat(row['expiration']),_number(row['strike']),row['option_type'])
        if when.date()>contract.expiration:raise ValueError('Post-expiry futures option execution')
        fills.append(OptionFill(row['source'],row['order'],when,contract,_number(row['quantity']),row['effect'],_number(row['price'])))
    if [f.row_id for f in fills]!=lineage['removed_futures_row_ids']:raise ValueError('Futures journal and derivative lineage differ')
    if any(a.executed_at>b.executed_at for a,b in zip(fills,fills[1:])):raise ValueError('Unordered futures execution journal')
    for row in r['deliveries']:
        if not isinstance(row,dict) or set(row)!={'source','cash_source','executed_at','underlying','expiration','strike','option_type','quantity','reported_cash','fee_cash'}:
            raise ValueError('Invalid futures exercise journal fields')
        if any(not isinstance(row[k],str) or not re.fullmatch('ledger-row-[0-9]+',row[k]) for k in ('source','cash_source')):
            raise ValueError('Invalid futures delivery source identity')
        when=datetime.fromisoformat(row['executed_at']);expiry=date.fromisoformat(row['expiration'])
        if when.tzinfo is not None or not period_start<=when.date()<=period_end or when.date()!=expiry:
            raise ValueError('Invalid futures exercise date')
        deliveries.append(Delivery(row['source'],row['cash_source'],when,ESFuture(row['underlying']),expiry,
            _number(row['strike']),row['option_type'],_number(row['quantity']),_number(row['reported_cash']),_number(row['fee_cash'])))
    options,futures,_=replay(fills,deliveries)
    if amounts['fees_cash_usd']>sum((d.fee_cash for d in deliveries),Decimal(0)):
        raise ValueError('Total futures fees cannot omit recorded delivery fees')
    if options.positions() or futures.positions():raise ValueError('Futures journal does not produce the declared empty ending books')
    if (sum((f.cash for f in fills),Decimal(0))!=amounts['option_premium_cash_usd'] or
        options.realized!=amounts['option_realized_usd'] or futures.realized!=amounts['futures_realized_usd']):
        raise ValueError('Futures financial values disagree with source journal replay')
    return FuturesMonth(account_id,draft_id,period_start,period_end,r['source_revision'],amounts['option_realized_usd'],
        amounts['futures_realized_usd'],amounts['fees_cash_usd'],amounts['net_realized_usd'],amounts['cash_sweeps_usd'],text)


def reference(record):
    data=record.to_dict()
    return dict(format='campaigniq.futures_generation_reference',version=1,
        artifact_key=record.period_start.strftime('%Y-%m')+'-futures.json',sha256=sha256(record.record_json.encode()).hexdigest(),
        account_id=record.account_id,draft_id=record.draft_id,source_revision=record.source_revision,
        source_refs=data['source_refs'])


def load_published(storage,*,period_start,period_end):
    prefix=period_start.strftime('%Y-%m')+'-';key=prefix+'futures.json'
    receipt_key=prefix+'account-publication-receipt.json'
    receipt=json.loads(storage.read_text(receipt_key),object_pairs_hook=_object) if storage.exists(receipt_key) else {}
    if not isinstance(receipt,dict):raise ValueError('Invalid financial publication receipt')
    descriptor=receipt.get('futures_generation')
    if not storage.exists(key) and descriptor is None:return None
    marker_key=finalized_month_marker_key(period_end=period_end)
    cutover=publication_cutover_from_storage(storage)
    if (cutover is None or period_end<cutover or not storage.exists(marker_key) or
        not is_month_published_in_storage(storage,period_end=period_end) or descriptor is None or
        receipt.get('format')!='campaigniq.deployment_account_publication' or type(receipt.get('version')) is not int or receipt['version']!=1 or
        receipt.get('period_end')!=period_end.isoformat()):raise ValueError('Unverified futures generation visibility or receipt')
    marker=json.loads(storage.read_text(marker_key),object_pairs_hook=_object)
    if (not isinstance(marker,dict) or set(marker)!={'format','version','period_end'} or marker['format']!='campaigniq.finalized_month' or
        type(marker['version']) is not int or marker['version']!=1 or marker['period_end']!=period_end.isoformat()):
        raise ValueError('Invalid futures generation visibility marker')
    text=storage.read_text(key)
    record=parse_record(text,account_id=receipt['account_id'],draft_id=receipt['draft_id'],period_start=period_start,period_end=period_end)
    if (json.dumps(descriptor,sort_keys=True)!=json.dumps(reference(record),sort_keys=True) or
            type(receipt.get('draft_revision')) is not int or receipt['draft_revision']!=record.source_revision):
        raise ValueError('Futures generation differs from publication receipt')
    return record
