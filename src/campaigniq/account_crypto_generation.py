"""Receipt-bound crypto financial candidate; no artifact writes or publication.

The existing strict export importer remains unchanged. This separate record
retains the entire approved calculation and its unresolved source differences.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re

from campaigniq.account_crypto_statement_sources import saved_preview, load_receipt
from campaigniq.crypto_statement_policy_preview import POLICY

FORMAT='campaigniq.approved_crypto_financial_candidate'


def _json(value):return json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n'
def _hash(value):return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None
def _decimal(value):
    if not isinstance(value,str):raise ValueError('Exact crypto decimal text required')
    try:result=Decimal(value)
    except InvalidOperation as error:raise ValueError('Invalid crypto decimal') from error
    if not result.is_finite():raise ValueError('Finite crypto decimal required')
    return result


@dataclass(frozen=True)
class CryptoFinancialCandidate:
    record_json:str
    @property
    def sha256(self):return sha256(self.record_json.encode()).hexdigest()
    def to_dict(self):return json.loads(self.record_json)


def _unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate crypto financial field')
        result[key]=value
    return result


def parse_record(text,*,account_id,draft_id,revision):
    """Validate exact scope and economics; source authenticity requires rebuild.

    A self-consistent JSON file alone never establishes owner approval. Call
    verify_candidate in the currently authorized account snapshot before use.
    """
    body=json.loads(text,object_pairs_hook=_unique)
    fields={'format','version','account_id','draft_id','source_revision','policy_id',
        'source_receipt_sha256','crypto_account_digest','approval','calculation',
        'calculation_sha256','publication_authorized','financial_state_written'}
    if (not isinstance(body,dict) or set(body)!=fields or body['format']!=FORMAT or
        type(body['version']) is not int or body['version']!=1 or
        type(revision) is not int or revision<0 or type(body['source_revision']) is not int or
        (body['account_id'],body['draft_id'],body['source_revision'])!=(account_id,str(draft_id),revision) or
        not isinstance(account_id,str) or not account_id or not str(draft_id) or
        body['policy_id']!=POLICY or body['publication_authorized'] is not False or body['financial_state_written'] is not False or
        not all(_hash(body[key]) for key in ('source_receipt_sha256','crypto_account_digest','calculation_sha256'))):
        raise ValueError('Crypto financial candidate scope or protocol differs')
    approval=body['approval']
    if (not isinstance(approval,dict) or set(approval)!={'source_receipt_verified','account_association','broker_verified_account_link'} or
        approval.get('source_receipt_verified') is not True or approval.get('broker_verified_account_link') is not False or
        approval!={'source_receipt_verified':True,'account_association':'owner_confirmed','broker_verified_account_link':False}):
        raise ValueError('Explicit owner source approval evidence required')
    calculation=body['calculation']
    if (not isinstance(calculation,dict) or calculation.get('format')!='campaigniq.crypto_statement_policy_preview' or
        type(calculation.get('version')) is not int or calculation['version']!=1 or calculation.get('policy_id')!=POLICY or
        body['calculation_sha256']!=sha256(_json(calculation).encode()).hexdigest()):
        raise ValueError('Crypto calculation fingerprint differs')
    if any(calculation.get(key) is not False for key in ('original_discrepancies_resolved','policy_approved',
        'account_association_confirmed','financial_state_written','publication_authorized')):
        raise ValueError('Original crypto calculation approval state must be preserved')
    start=date.fromisoformat(calculation['period_start']);end=date.fromisoformat(calculation['period_end'])
    from calendar import monthrange
    if start.day!=1 or end!=start.replace(day=monthrange(start.year,start.month)[1]):
        raise ValueError('Exact crypto calendar month required')
    review=calculation['review']
    if (review.get('all_matched_evidence_checks_pass') is not True or
        calculation['review_sha256']!=sha256(_json(review).encode()).hexdigest() or
        review.get('source_sha256')!=calculation.get('source_sha256')):
        raise ValueError('Crypto discrepancy review differs')
    if (set(calculation['source_sha256'])!={'trade_history','previous_crypto_statement','current_crypto_statement'} or
        not all(_hash(v) for v in calculation['source_sha256'].values())):
        raise ValueError('Exact crypto original fingerprints required')
    totals=calculation['totals']
    # Preserve unknown costs as null; never substitute market value or zero.
    values={k:_decimal(v) for k,v in totals.items() if v is not None}
    if (values['proposed_opening_cash_usd']+values['net_external_funding_usd']-
        values['purchases_including_fees_usd']+values['net_sale_proceeds_usd']!=values['proposed_ending_cash_usd']):
        raise ValueError('Crypto financial cash roll-forward differs')
    if (values['current_holdings_plus_cash_usd']-values['preceding_holdings_plus_cash_usd']-
        values['net_external_funding_usd']!=values['proposed_value_change_after_external_funding_usd']):
        raise ValueError('Crypto value change differs')
    lots=calculation['ending_lots'];quantities=calculation['ending_quantities']
    if set(lots)!=set(quantities):raise ValueError('Crypto lot inventory differs')
    known=Decimal(0);complete=True
    for pair,rows in lots.items():
        quantity=Decimal(0)
        for lot in rows:
            q=_decimal(lot['quantity'])
            if q<=0:raise ValueError('Positive crypto lot quantity required')
            quantity+=q
            if lot['cost_usd'] is None:complete=False
            else:
                cost=_decimal(lot['cost_usd'])
                if cost<0:raise ValueError('Negative crypto lot cost')
                known+=cost
        if quantity!=_decimal(quantities[pair]):raise ValueError('Crypto ending quantity differs')
    if (type(calculation['ending_cost_basis_complete']) is not bool or calculation['ending_cost_basis_complete']!=complete or
        values['known_ending_lot_cost_usd']!=known or
        (totals['full_ending_cost_basis_usd'] is None)!= (not complete) or
        (complete and values['full_ending_cost_basis_usd']!=known)):
        raise ValueError('Crypto ending cost basis differs')
    return CryptoFinancialCandidate(_json(body))


def build_candidate(service,session,draft):
    checked=saved_preview(service,session,draft)
    receipt=load_receipt(session,draft)
    calculation=checked['calculation']
    body=dict(format=FORMAT,version=1,account_id=session.account.account_id,draft_id=str(draft.id),
        source_revision=draft.revision,policy_id=POLICY,source_receipt_sha256=checked['receipt_sha256'],
        crypto_account_digest=receipt['crypto_account_digest'],
        approval=dict(source_receipt_verified=True,account_association='owner_confirmed',broker_verified_account_link=False),
        calculation=calculation,calculation_sha256=sha256(_json(calculation).encode()).hexdigest(),
        publication_authorized=False,financial_state_written=False)
    return parse_record(_json(body),account_id=session.account.account_id,draft_id=draft.id,revision=draft.revision)


def verify_candidate(service,session,draft,text):
    actual=parse_record(text,account_id=session.account.account_id,draft_id=draft.id,revision=draft.revision)
    expected=build_candidate(service,session,draft)
    if actual!=expected:raise ValueError('Crypto financial candidate differs from current approved originals')
    return actual


def generation_preview(service,session,draft):
    record=build_candidate(service,session,draft)
    return dict(format='campaigniq.account_crypto_financial_generation_preview',version=1,
        artifact=record.to_dict(),artifact_text=record.record_json,artifact_sha256=record.sha256,
        candidate_only=True,publication_authorized=False,financial_state_written=False,
        publication_blocker='Complete monthly writer and restricted-role generation rehearsal remain pending.')
