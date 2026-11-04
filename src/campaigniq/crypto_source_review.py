"""Read-only comparison of exact crypto originals. Never authorizes publication.

Reported amounts and printed opening balances are retained even when a preceding
statement supplies a different carry-forward. No tolerance or correction is applied
to the production crypto importer, cost basis or stored financial records.
"""
from calendar import monthrange
from collections import Counter,defaultdict
from datetime import date
from decimal import Decimal,ROUND_DOWN,ROUND_HALF_UP
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import re

from campaigniq.importers.schwab.crypto_statement_reader import read_crypto_statement
from campaigniq.importers.thinkorswim.crypto_reader import read_crypto_report
from campaigniq.pdf_text import extract_pdf_text

D=Decimal
ZERO=D(0)


def _amount(value):
    if not isinstance(value,str):raise ValueError('Exact crypto decimal text required')
    result=D(value)
    if not result.is_finite():raise ValueError('Nonfinite crypto amount')
    return result


def _fill_key(row,*,export):
    return (row['executed_at'][:10] if export else row['date'],row['side'] if export else row['action'],
        row['pair'],_amount(row['quantity']),_amount(row['fee_usd']))


def _net(fill):
    sign=-1 if fill['side']=='BUY' else 1
    return sign*_amount(fill['principal_usd'])-_amount(fill['fee_usd'])


def _price_relation(full,printed):
    if full==printed:return 'exact'
    precision=D(1).scaleb(printed.as_tuple().exponent)
    return 'consistent_with_printed_truncation' if full.quantize(precision,rounding=ROUND_DOWN)==printed else 'different'


def compare_sources(export,previous,current):
    """Detached review; no mutation, inferred basis, state repair or source precedence."""
    start=date.fromisoformat(current['period_start']);end=date.fromisoformat(current['period_end'])
    prior_start=date.fromisoformat(previous['period_start']);prior_end=start-date.resolution
    if (start.day!=1 or end!=start.replace(day=monthrange(start.year,start.month)[1]) or
        prior_start!=prior_end.replace(day=1) or previous['period_end']!=prior_end.isoformat() or
        export['period_start']!=start.isoformat() or export['period_end']!=end.isoformat()):
        raise ValueError('Exact consecutive calendar months and selected export required')
    suffix=re.search(r'(\d{4})$',export.get('account') or '')
    if suffix is None or previous['account_suffix']!=current['account_suffix'] or suffix[1]!=current['account_suffix']:
        raise ValueError('Crypto account suffix differs across sources; full account ownership is not inferred')
    for statement in (previous,current):
        for row in statement['transactions']:
            if not statement['period_start']<=row['date']<=statement['period_end']:
                raise ValueError('Crypto statement activity outside its stated period')
            for key in ('quantity','price_usd','fee_usd','amount_usd'):_amount(row[key])
        for key in ('beginning_cash_usd','ending_cash_usd'):_amount(statement[key])
    fills=defaultdict(list);observed=defaultdict(list)
    for fill in export['fills']:
        if fill['principal_usd'] is None or fill['fee_usd'] is None:
            raise ValueError('Exact export principal and fee evidence required for every fill')
        fills[_fill_key(fill,export=True)].append(fill)
    for number,row in enumerate(current['transactions']):
        if row['action'] in ('BUY','SELL'):observed[_fill_key(row,export=False)].append((number,row))
    pairs=[];unmatched=[];ambiguous=[]
    for key in sorted(set(fills)|set(observed)):
        left=fills[key];right=observed[key]
        if len(left)!=len(right):
            unmatched.append(dict(date=key[0],side=key[1],pair=key[2],quantity=str(key[3]),
                fee_usd=str(key[4]),export_count=len(left),statement_count=len(right)))
            continue
        remaining=list(left)
        for ordinal,row in right:
            candidates=[f for f in remaining if _price_relation(_amount(f['price_usd']),_amount(row['price_usd']))!='different']
            # Repeated identical legs remain separate. Distinct possible source
            # economics are ambiguous; never pair them merely by row order.
            signatures={(_amount(f['price_usd']),_amount(f['principal_usd']),_amount(f['fee_usd'])) for f in candidates}
            if not candidates or len(signatures)!=1:
                ambiguous.append(dict(statement_transaction_index=ordinal,candidate_count=len(candidates)))
                continue
            fill=candidates[0];remaining.remove(fill)
            pairs.append(dict(export_fill_id=fill['fill_id'],statement_transaction_index=ordinal,
                date=key[0],side=key[1],pair=key[2],quantity=fill['quantity'],fee_usd=fill['fee_usd'],
                export_price_usd=fill['price_usd'],statement_price_usd=row['price_usd'],
                price_relationship=_price_relation(_amount(fill['price_usd']),_amount(row['price_usd'])),
                export_principal_usd=fill['principal_usd'],export_net_cash_usd=str(_net(fill)),
                statement_net_cash_usd=row['amount_usd'],
                statement_minus_export_cash_usd=str(_amount(row['amount_usd'])-_net(fill))))
    funding=Counter((r['executed_at'][:10],_amount(r['amount_usd'])) for r in export['cash_ledger'] if r['type']=='FND')
    printed_funding=Counter((r['date'],_amount(r['amount_usd'])) for r in current['transactions'] if r['action'] in ('DEPOSIT','WITHDRAWAL'))
    unsupported=[r['ledger_id'] for r in export['cash_ledger'] if r['type'] not in ('BAL','FND','TRD')]
    net={};position_checks=[]
    for row in current['transactions']:
        if row['action'] in ('BUY','SELL'):
            net[row['pair']]=net.get(row['pair'],ZERO)+(1 if row['action']=='BUY' else -1)*_amount(row['quantity'])
    for pair in sorted(set(net)|set(previous['holdings'])|set(current['holdings'])):
        opening=_amount(previous['holdings'][pair]['quantity']) if pair in previous['holdings'] else ZERO
        ending=_amount(current['holdings'][pair]['quantity']) if pair in current['holdings'] else ZERO
        position_checks.append(dict(pair=pair,previous_statement_quantity=str(opening),net_statement_activity=str(net.get(pair,ZERO)),
            expected_ending_quantity=str(opening+net.get(pair,ZERO)),current_statement_quantity=str(ending),
            delta=str(ending-opening-net.get(pair,ZERO))))
    cash_activity=sum((_amount(r['amount_usd']) for r in current['transactions']),ZERO)
    prior_cash=_amount(previous['ending_cash_usd']);ending_cash=_amount(current['ending_cash_usd'])
    printed_opening=_amount(current['beginning_cash_usd'])
    ledger_activity=sum((_amount(r['amount_usd'])+_amount(r['fee_cash_change_usd'])
        for r in export['cash_ledger'] if r['type']!='BAL'),ZERO)
    opening_export=_amount(export['opening_cash_usd']) if export.get('opening_cash_usd') is not None else None
    ending_export=_amount(export['last_reported_cash_usd']) if export.get('last_reported_cash_usd') is not None else None
    displayed_value_checks=[]
    for pair,holding in sorted(current['holdings'].items()):
        calculated=(_amount(holding['quantity'])*_amount(holding['price_usd'])).quantize(D('.01'),rounding=ROUND_HALF_UP)
        displayed_value_checks.append(dict(pair=pair,calculated_to_cents=str(calculated),reported=holding['market_value_usd'],
            matches=calculated==_amount(holding['market_value_usd'])))
    statements={}
    for name,s in (('previous',previous),('current',current)):
        activity=sum((_amount(r['amount_usd']) for r in s['transactions']),ZERO)
        statements[name]=dict(printed_beginning_cash_usd=s['beginning_cash_usd'],printed_ending_cash_usd=s['ending_cash_usd'],
            listed_net_activity_usd=str(activity),printed_cash_rollforward_delta_usd=str(_amount(s['beginning_cash_usd'])+activity-_amount(s['ending_cash_usd'])))
    cash_differences=[p for p in pairs if _amount(p['statement_minus_export_cash_usd'])!=ZERO]
    checks=dict(complete_export_coverage=bool(export.get('export_start') and export.get('export_end') and
            export['export_start']<=start.isoformat() and export['export_end']>=end.isoformat()),
        export_execution_and_ledger_fills_match=export['fill_control_reconciled'] is True,
        all_partial_fills_paired=bool(export['fills']) and len(pairs)==len(export['fills']) and not unmatched and not ambiguous,
        deposits_and_withdrawals_match=funding==printed_funding,unsupported_cash_activity_absent=not unsupported,
        ending_quantities_match_preceding_statement_and_activity=all(_amount(p['delta'])==ZERO for p in position_checks),
        ending_cash_matches=ending_export==ending_cash,
        cash_balances_using_preceding_statement=prior_cash+cash_activity==ending_cash,
        displayed_market_values_match=all(p['matches'] for p in displayed_value_checks))
    exceptions=[]
    if prior_cash!=printed_opening:exceptions.append('Current printed opening cash differs from the preceding statement closing cash.')
    if opening_export!=prior_cash:exceptions.append('Export opening cash differs from the preceding statement closing cash.')
    if cash_differences:exceptions.append('Individual statement trade cash amounts differ from export principal plus fees.')
    if not export['cash_control_reconciled']:exceptions.append('Export running cash balances do not all reconcile exactly.')
    if any(_amount(s['printed_cash_rollforward_delta_usd'])!=ZERO for s in statements.values()):
        exceptions.append('Printed statement beginning balances and listed activity do not roll forward exactly.')
    if any(p['price_relationship']!='exact' for p in pairs):exceptions.append('Some printed prices are consistent with truncation of fuller export prices; a source presentation policy requires review.')
    return dict(format='campaigniq.crypto_source_review',version=1,period_start=start.isoformat(),period_end=end.isoformat(),
        account_suffix=current['account_suffix'],account_identity_evidence='matching suffix only; full account association not established',
        checks=checks,paired_fills=pairs,unmatched_fill_groups=unmatched,ambiguous_fill_matches=ambiguous,
        position_checks=position_checks,displayed_market_value_checks=displayed_value_checks,statement_cash=statements,
        cash=dict(previous_statement_closing_usd=str(prior_cash),current_printed_opening_usd=str(printed_opening),
            export_opening_usd=str(opening_export) if opening_export is not None else None,
            export_net_activity_usd=str(ledger_activity),statement_net_activity_usd=str(cash_activity),
            export_overall_rollforward_delta_usd=str(opening_export+ledger_activity-ending_export) if opening_export is not None and ending_export is not None else None,
            statement_rollforward_using_preceding_closing_delta_usd=str(prior_cash+cash_activity-ending_cash),
            current_statement_ending_usd=str(ending_cash),export_ending_usd=str(ending_export) if ending_export is not None else None),
        export_running_balance_deltas_usd=list(export['cash_control_deltas_usd']),trade_cash_differences=cash_differences,
        exceptions_requiring_review=exceptions,all_matched_evidence_checks_pass=all(checks.values()),
        all_controls_reconciled=all(checks.values()) and not exceptions,review_required=bool(exceptions) or not all(checks.values()),
        opening_cost_basis_established=False,source_adjustments_applied=False,publication_authorized=False)


def review_files(history,previous_pdf,current_pdf,*,year,month):
    start=date(year,month,1);end=start.replace(day=monthrange(year,month)[1]);prior_end=start-date.resolution
    paths={name:Path(path).resolve(strict=True) for name,path in
        (('trade_history',history),('previous_crypto_statement',previous_pdf),('current_crypto_statement',current_pdf))}
    if len(set(paths.values()))!=3:raise ValueError('Three distinct source originals required')
    hashes={name:sha256(path.read_bytes()).hexdigest() for name,path in paths.items()}
    with TemporaryDirectory(prefix='campaigniq-crypto-source-review-') as tmp:
        root=Path(tmp);statements={}
        for name,first,last in (('previous_crypto_statement',prior_end.replace(day=1),prior_end),('current_crypto_statement',start,end)):
            if not paths[name].read_bytes().startswith(b'%PDF-'):raise ValueError('Original crypto statement PDFs required')
            text=extract_pdf_text(paths[name],root/(name+'.txt'))
            statements[name]=read_crypto_statement(text,period_start=first,period_end=last)
        export=read_crypto_report(paths['trade_history'],period_start=start,period_end=end)
        if export is None:raise ValueError('Crypto evidence absent from the selected export')
        result=compare_sources(export,statements['previous_crypto_statement'],statements['current_crypto_statement'])
    if hashes!={name:sha256(path.read_bytes()).hexdigest() for name,path in paths.items()}:
        raise ValueError('Source changed during crypto review')
    result['source_sha256']=hashes;result['original_bytes_preserved']=True
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
    if os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):parser.error('Local read-only source review required')
    try:result=review_files(args.trade_history,args.previous_statement,args.current_statement,year=args.year,month=args.month)
    except (ValueError,OSError,RuntimeError) as error:
        print(json.dumps(dict(format='campaigniq.crypto_source_review',version=1,publication_authorized=False,
            review_completed=False,error_type=type(error).__name__)))
        return 1
    print(json.dumps(result,sort_keys=True,indent=2))
    # A review can finish while unresolved differences still block reconciliation.
    return 0 if result['all_controls_reconciled'] else 2


if __name__=='__main__':raise SystemExit(main())
