"""Pure verified loading of a complete owner-approved Forex generation."""
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
import re
from campaigniq.forex_daily_financial_candidate import POLICY
from campaigniq.account_forex_daily_sources import unique
from campaigniq.persistence.monthly_publication import is_month_published_in_storage

SUFFIXES=('lot-book.json','position-journal.json','forex-settlement-attributions.json',
    'forex-source-resolution.json','market-applicability.json','import-provenance.json',
    'realized-attributions.json','lifecycle-transitions.json','boundary-completeness.json')


def reference(body,text):
    return dict(format='campaigniq.approved_forex_generation_reference',version=1,
        artifact_key=body['period_start'][:7]+'-forex-approved-generation.json',
        sha256=sha256(text.encode()).hexdigest(),approval_receipt_sha256=body['approval_receipt_sha256'],
        account_id=body['account_id'],draft_id=body['draft_id'],source_revision=body['source_revision'])


def load_published(storage,*,period_start,period_end):
    try:return _load_published(storage,period_start=period_start,period_end=period_end)
    except (KeyError,TypeError,AttributeError) as error:
        raise ValueError('Incomplete or invalid approved Forex generation') from error


def _load_published(storage,*,period_start,period_end):
    prefix=period_start.strftime('%Y-%m')+'-';key=prefix+'forex-approved-generation.json'
    receipt_key=prefix+'account-publication-receipt.json'
    receipt=json.loads(storage.read_text(receipt_key),object_pairs_hook=unique) if storage.exists(receipt_key) else {}
    descriptor=receipt.get('approved_forex_generation')
    audit_key=prefix+'forex-source-resolution.json'
    audit=json.loads(storage.read_text(audit_key),object_pairs_hook=unique) if storage.exists(audit_key) else {}
    if not storage.exists(key) and descriptor is None:
        if audit.get('version')==3:raise ValueError('Approved Forex generation or receipt is missing')
        return None
    if not storage.exists(key) or not is_month_published_in_storage(storage,period_end=period_end) or descriptor is None:
        raise ValueError('Complete approved Forex visibility and receipt required')
    text=storage.read_text(key);body=json.loads(text,object_pairs_hook=unique)
    fields={'format','version','policy_id','account_id','draft_id','source_revision','period_start','period_end',
        'approval_receipt_sha256','approved_source_snapshot','source_lineage','financials','canonical_artifact_sha256'}
    if (not isinstance(body,dict) or set(body)!=fields or body['format']!='campaigniq.approved_forex_month' or
        type(body['version']) is not int or body['version']!=1 or body['policy_id']!=POLICY or
        (body['period_start'],body['period_end'])!=(str(period_start),str(period_end)) or
        (period_start,period_end)!=(date(2026,10,1),date(2026,10,31)) or
        type(body['source_revision']) is not int or body['source_revision']!=receipt.get('draft_revision') or
        type(receipt.get('draft_revision')) is not int or receipt.get('forex_applicable') is not True or
        receipt.get('format')!='campaigniq.deployment_account_publication' or
        type(receipt.get('version')) is not int or receipt['version']!=1 or receipt.get('period_end')!=str(period_end) or
        (body['account_id'],body['draft_id'])!=(receipt.get('account_id'),receipt.get('draft_id'))):
        raise ValueError('Approved Forex generation scope differs')
    if descriptor!=reference(body,text):raise ValueError('Approved Forex generation receipt differs')
    hashes=body['canonical_artifact_sha256']
    if not isinstance(hashes,dict) or set(hashes)!={prefix+s for s in SUFFIXES}:
        raise ValueError('Complete Forex generation artifact inventory required')
    for name,value in hashes.items():
        if (not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value) or
            not storage.exists(name) or sha256(storage.read_text(name).encode()).hexdigest()!=value):
            raise ValueError('Approved Forex canonical artifact differs')
    source=body['approved_source_snapshot']
    if not isinstance(source,dict):raise ValueError('Invalid approved Forex source snapshot')
    if (source.get('financial_policy_approved') is not True or source.get('publication_authorized') is not False or
        source.get('approval_receipt_sha256')!=body['approval_receipt_sha256'] or
        source['candidate']['artifact']['calculation']['totals']!=body['financials']):
        raise ValueError('Approved Forex source/financial snapshot differs')
    from decimal import InvalidOperation
    try:amounts={k:Decimal(v) for k,v in body['financials'].items() if isinstance(v,str)}
    except (InvalidOperation,TypeError) as error:raise ValueError('Invalid Forex money') from error
    if (set(amounts)!={'opening_cash_usd','trading_usd','financing_usd','ending_cash_usd','net_cash_change_usd'} or
        any(not v.is_finite() for v in amounts.values()) or
        amounts['opening_cash_usd']+amounts['trading_usd']+amounts['financing_usd']!=amounts['ending_cash_usd'] or
        amounts['net_cash_change_usd']!=amounts['trading_usd']+amounts['financing_usd']):
        raise ValueError('Approved Forex generation cash equation differs')
    from campaigniq.persistence.forex_settlement_attribution_store import deserialize_forex_settlement_attributions
    settled=deserialize_forex_settlement_attributions(storage.read_text(prefix+'forex-settlement-attributions.json'))
    if (sum((a.gain_loss for a in settled.attributions),Decimal(0))!=amounts['trading_usd'] or
        len({a.settlement.order_id for a in settled.attributions})!=len(settled.attributions)):
        raise ValueError('Approved Forex attribution totals differ')
    return body
