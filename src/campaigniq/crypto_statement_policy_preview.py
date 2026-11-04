"""Proposed statement-backed crypto economics. Read-only, unapproved and unpublished.

Keeps original export observations and the complete discrepancy review. This is
not the production crypto-month format and cannot replace a published month.
"""
from calendar import monthrange
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import re

from campaigniq.crypto_source_review import compare_sources
from campaigniq.importers.schwab.crypto_statement_reader import read_crypto_statement
from campaigniq.importers.thinkorswim.crypto_reader import read_crypto_report
from campaigniq.pdf_text import extract_pdf_text

ZERO=Decimal(0)
POLICY='statement_trade_cash_and_preceding_closing_balances_v1'
SOURCE_ROLES={'trade_history','previous_crypto_statement','current_crypto_statement'}


def _json(value):return json.dumps(value,sort_keys=True,indent=2)+'\n'
def _money(value):
    if not isinstance(value,str):raise ValueError('Exact decimal text required')
    result=Decimal(value)
    if not result.is_finite():raise ValueError('Nonfinite crypto policy value')
    return result


def _usd(value):
    result=_money(value)
    if result!=result.quantize(Decimal('.01')):raise ValueError('Statement cash and fees must have exact cent precision')
    return result


@dataclass(frozen=True)
class CryptoPolicyPreview:
    text:str
    @property
    def sha256(self):return sha256(self.text.encode()).hexdigest()
    def to_dict(self):return json.loads(self.text)


def build_preview(export,previous,current,*,source_sha256):
    """Calculate a proposed source choice; never accepts/waives the differences."""
    if (not isinstance(source_sha256,dict) or set(source_sha256)!=SOURCE_ROLES or
        any(not isinstance(v,str) or re.fullmatch('[0-9a-f]{64}',v) is None for v in source_sha256.values())):
        raise ValueError('All three original source fingerprints are required')
    review=compare_sources(export,previous,current)
    if not review['all_matched_evidence_checks_pass']:
        raise ValueError('Conflicting or incomplete evidence cannot produce a statement-backed candidate')
    # A summary value is never an opening cost basis. Preserve carried units with
    # unknown cost until separate retained basis evidence can be verified.
    lots={};opening_quantities={}
    for pair,holding in sorted(previous['holdings'].items()):
        quantity=_money(holding['quantity'])
        if quantity<ZERO:raise ValueError('Negative crypto opening holdings unsupported')
        opening_quantities[pair]=str(quantity)
        lots[pair]=[dict(quantity=str(quantity),cost_usd=None,source='previous_crypto_statement_holdings',
            basis_status='unknown; market value is not cost basis')] if quantity else []
    fills_by_id={f['fill_id']:f for f in export['fills']}
    if len(fills_by_id)!=len(export['fills']):raise ValueError('Duplicate export fill identity')
    allocations=[];sales=[];purchases=ZERO;sale_cash=ZERO;fees=ZERO
    source_choice_rows=[]
    ordered=sorted(review['paired_fills'],key=lambda row:(fills_by_id[row['export_fill_id']]['executed_at'],
        int(row['export_fill_id'].rsplit('-',1)[1])))
    for pair in ordered:
        original=fills_by_id[pair['export_fill_id']]
        printed=current['transactions'][pair['statement_transaction_index']]
        quantity=_money(pair['quantity']);fee=_usd(pair['fee_usd']);cash=_usd(printed['amount_usd'])
        if quantity<=ZERO or fee<ZERO:raise ValueError('Invalid proposed crypto quantity or fee')
        rows=lots.setdefault(pair['pair'],[]);fees+=fee
        source=dict(export_fill_id=pair['export_fill_id'],statement_transaction_index=pair['statement_transaction_index'],
            executed_at=original['executed_at'],pair=pair['pair'],side=pair['side'],quantity=pair['quantity'],
            export_price_usd=pair['export_price_usd'],printed_price_usd=pair['statement_price_usd'],fee_usd=str(fee),
            proposed_statement_net_cash_usd=str(cash),original_export_net_cash_usd=pair['export_net_cash_usd'],
            statement_minus_export_cash_usd=pair['statement_minus_export_cash_usd'])
        if pair['side']=='BUY':
            cost=-cash
            if cost<=ZERO or cost<fee:raise ValueError('Statement buy amount cannot establish a valid cost')
            purchases+=cost
            rows.append(dict(quantity=str(quantity),cost_usd=str(cost),source=pair['export_fill_id'],
                statement_transaction_index=pair['statement_transaction_index'],opened_at=original['executed_at'],
                basis_status='proposed statement amount including fees'))
            source['proposed_principal_usd']=str(cost-fee)
        else:
            if cash<ZERO:raise ValueError('Negative statement net sale cash unsupported')
            sale_cash+=cash;source['proposed_principal_usd']=str(cash+fee)
            remaining=quantity;cost=ZERO;known=True
            while remaining and rows:
                lot=rows[0];q=_money(lot['quantity']);used=min(q,remaining)
                old_cost=_money(lot['cost_usd']) if lot['cost_usd'] is not None else None
                allocated=old_cost*used/q if old_cost is not None else None
                if allocated is None:known=False
                else:cost+=allocated
                allocations.append(dict(sale_fill_id=pair['export_fill_id'],opening_source=lot['source'],
                    quantity=str(used),cost_usd=str(allocated) if allocated is not None else None))
                remaining-=used
                if used==q:rows.pop(0)
                else:
                    lot['quantity']=str(q-used)
                    lot['cost_usd']=str(old_cost-allocated) if allocated is not None else None
            if remaining:raise ValueError('Crypto sale exceeds statement-backed available units')
            sales.append(dict(export_fill_id=pair['export_fill_id'],net_proceeds_usd=str(cash),
                fifo_cost_usd=str(cost) if known else None,realized_pnl_usd=str(cash-cost) if known else None))
        source_choice_rows.append(source)
    ending={p:str(sum((_money(l['quantity']) for l in rows),ZERO)) for p,rows in sorted(lots.items())}
    for pair in set(ending)|set(current['holdings']):
        reported=_money(current['holdings'][pair]['quantity']) if pair in current['holdings'] else ZERO
        if _money(ending.get(pair,'0'))!=reported:raise ValueError('Proposed FIFO units differ from current statement')
    funding=sum((_usd(t['amount_usd']) for t in current['transactions'] if t['action'] in ('DEPOSIT','WITHDRAWAL')),ZERO)
    opening_cash=_usd(previous['ending_cash_usd']);ending_cash=_usd(current['ending_cash_usd'])
    if opening_cash+funding-purchases+sale_cash!=ending_cash:raise ValueError('Proposed statement cash roll-forward fails')
    opening_value=sum((_usd(h['market_value_usd']) for h in previous['holdings'].values()),ZERO)+opening_cash
    closing_value=sum((_usd(h['market_value_usd']) for h in current['holdings'].values()),ZERO)+ending_cash
    realized=sum((_money(s['realized_pnl_usd']) for s in sales),ZERO) if all(s['realized_pnl_usd'] is not None for s in sales) else None
    known_cost=sum((_money(l['cost_usd']) for rows in lots.values() for l in rows if l['cost_usd'] is not None),ZERO)
    all_basis=all(l['cost_usd'] is not None for rows in lots.values() for l in rows)
    review['source_sha256']=deepcopy(source_sha256)
    body=dict(format='campaigniq.crypto_statement_policy_preview',version=1,policy_id=POLICY,
        period_start=current['period_start'],period_end=current['period_end'],account_suffix=current['account_suffix'],
        source_sha256=deepcopy(source_sha256),review_sha256=sha256(_json(review).encode()).hexdigest(),review=review,
        proposed_source_choices=dict(opening_cash='preceding statement closing cash',opening_units='preceding statement closing holdings',
            trade_cash_and_fees='current statement; fees already included in trade cash',
            quantities_and_execution_times='exact matched export executions',
            full_precision_prices='retained from export; not used to overwrite statement cash',
            displayed_ending_values='current dated statement; not the undated export snapshot'),
        source_choice_rows=source_choice_rows,opening_quantities=opening_quantities,ending_quantities=ending,
        ending_lots=lots,fifo_allocations=allocations,realized_sales=sales,
        totals=dict(proposed_opening_cash_usd=str(opening_cash),net_external_funding_usd=str(funding),
            purchases_including_fees_usd=str(purchases),net_sale_proceeds_usd=str(sale_cash),
            fees_already_in_trade_cash_usd=str(fees),proposed_ending_cash_usd=str(ending_cash),
            proposed_realized_sales_pnl_usd=str(realized) if realized is not None else None,
            known_ending_lot_cost_usd=str(known_cost),full_ending_cost_basis_usd=str(known_cost) if all_basis else None,
            preceding_holdings_plus_cash_usd=str(opening_value),current_holdings_plus_cash_usd=str(closing_value),
            proposed_value_change_after_external_funding_usd=str(closing_value-opening_value-funding)),
        ending_cost_basis_complete=all_basis,realized_sales_basis_complete=realized is not None,
        intraday_statement_cash_order_established=False,policy_approved=False,account_association_confirmed=False,
        original_discrepancies_resolved=False,financial_state_written=False,publication_authorized=False,
        blockers=['Accounting source policy and exact discrepancies require review.',
            'Current owner account association and saved-source receipt integration are pending.',
            'Publication integration and restricted database verification are pending.'])
    return CryptoPolicyPreview(_json(body))


def preview_files(history,previous_pdf,current_pdf,*,year,month):
    start=date(year,month,1);end=start.replace(day=monthrange(year,month)[1]);prior_end=start-date.resolution
    paths={name:Path(path).resolve(strict=True) for name,path in
        (('trade_history',history),('previous_crypto_statement',previous_pdf),('current_crypto_statement',current_pdf))}
    if len(set(paths.values()))!=3:raise ValueError('Three distinct source originals required')
    hashes={name:sha256(path.read_bytes()).hexdigest() for name,path in paths.items()}
    with TemporaryDirectory(prefix='campaigniq-crypto-policy-preview-') as tmp:
        parsed={}
        for role,first,last in (('previous_crypto_statement',prior_end.replace(day=1),prior_end),('current_crypto_statement',start,end)):
            if not paths[role].read_bytes().startswith(b'%PDF-'):raise ValueError('Original statement PDFs required')
            parsed[role]=read_crypto_statement(extract_pdf_text(paths[role],Path(tmp)/(role+'.txt')),period_start=first,period_end=last)
        export=read_crypto_report(paths['trade_history'],period_start=start,period_end=end)
        if export is None:raise ValueError('Original export crypto evidence required')
        result=build_preview(export,parsed['previous_crypto_statement'],parsed['current_crypto_statement'],source_sha256=hashes)
    if hashes!={name:sha256(path.read_bytes()).hexdigest() for name,path in paths.items()}:raise ValueError('Source changed during preview')
    return result


def main():
    import argparse,os
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only',action='store_true',required=True)
    parser.add_argument('--trade-history',type=Path,required=True)
    parser.add_argument('--previous-statement',type=Path,required=True)
    parser.add_argument('--current-statement',type=Path,required=True)
    parser.add_argument('--year',type=int,required=True);parser.add_argument('--month',type=int,required=True)
    args=parser.parse_args()
    if os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):parser.error('Local preview only')
    try:result=preview_files(args.trade_history,args.previous_statement,args.current_statement,year=args.year,month=args.month)
    except (ValueError,OSError,RuntimeError) as error:
        print(_json(dict(format='campaigniq.crypto_statement_policy_preview',version=1,publication_authorized=False,
            preview_completed=False,error_type=type(error).__name__)),end='');return 1
    print(result.text,end='');return 0


if __name__=='__main__':raise SystemExit(main())
