"""Create and reverify a canonical financial candidate in an authorized snapshot.

No SQL writes or publication authority. The deployment publication guard remains
closed until the generation writer and restricted-role rehearsal are complete.
"""
from calendar import monthrange
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from campaigniq.account_futures_preview import connected_preview
from campaigniq.futures_accounting import read_fills,read_ledger
from campaigniq.futures_monthly_preview import project_history
from campaigniq.persistence.futures_month import SOURCE_ROLES,parse_record,reference,load_published
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader


def build_candidate(service,session,draft):
    checked=connected_preview(service,session,draft)
    if not checked['closing_positions_confirmed_by_owner'] or not checked['opening_positions_confirmed_by_owner']:
        raise ValueError('Current owner opening and ending confirmations are required')
    if checked['calculated_ending_options'] or checked['calculated_ending_futures']:
        raise ValueError('This generation profile requires a flat ending futures book')
    raw=session.drafts.document(draft.id,'thinkorswim_trade_history')[2]
    refs={role:sha256(session.drafts.document(draft.id,role)[2]).hexdigest() for role in sorted(SOURCE_ROLES)}
    with TemporaryDirectory(prefix='campaigniq-futures-generation-') as temporary:
        root=Path(temporary);path=root/'original.csv';path.write_bytes(raw)
        source=ThinkorswimSourceReader().read(str(path))
        fills=read_fills(source,draft.year,draft.month)
        _,deliveries,_,_,_=read_ledger(source,draft.year,draft.month)
        lineage=project_history(raw,checked,root/'derived-securities-history.csv')
    start=date(draft.year,draft.month,1);end=date(draft.year,draft.month,monthrange(draft.year,draft.month)[1])
    financials={key:checked[key] for key in ('option_premium_cash_usd','option_realized_usd','futures_realized_usd',
                                          'fees_cash_usd','realized_net_usd','cash_sweeps_usd')}
    financials['net_realized_usd']=financials.pop('realized_net_usd')
    financials={key:format(Decimal(value),'.2f') for key,value in financials.items()}
    body=dict(format='campaigniq.futures_month',version=1,account_id=session.account.account_id,draft_id=str(draft.id),
        period_start=start.isoformat(),period_end=end.isoformat(),source_revision=draft.revision,currency='USD',
        multiplier_usd='50',execution_time_basis='broker_export_local_time',
        evidence=dict(opening='owner_confirmed_empty',ending='owner_confirmed_empty',
            statement_account_association='owner_confirmed',independent_broker_position_verification=False),
        source_refs=refs,source_lineage=lineage,financials=financials,
        fills=[dict(source=f.row_id,order=f.order_id,executed_at=f.executed_at.isoformat(),
            underlying=f.contract.future.symbol,series=f.contract.series,expiration=f.contract.expiration.isoformat(),
            strike=str(f.contract.strike),option_type=f.contract.kind,quantity=str(f.quantity),effect=f.effect,price=str(f.price)) for f in fills],
        deliveries=[dict(source=d.row_id,cash_source=d.cash_row_id,executed_at=d.executed_at.isoformat(),
            underlying=d.future.symbol,expiration=d.expiration.isoformat(),strike=str(d.strike),option_type=d.kind,
            quantity=str(d.future_quantity),reported_cash=str(d.reported_cash),fee_cash=str(d.fee_cash)) for d in deliveries],
        ending_options={},ending_futures={})
    text=json.dumps(body,sort_keys=True,indent=2)+'\n'
    return parse_record(text,account_id=session.account.account_id,draft_id=str(draft.id),period_start=start,period_end=end)


def generation_preview(service,session,draft):
    record=build_candidate(service,session,draft)
    return dict(format='campaigniq.futures_financial_generation_preview',version=1,
        account_id=record.account_id,draft_id=record.draft_id,revision=record.source_revision,
        artifact=record.to_dict(),artifact_text=record.record_json,reference=reference(record),
        report=[dict(Component='Option result before fees',USD=str(record.option_realized_usd)),
                dict(Component='Underlying futures result before fees',USD=str(record.futures_realized_usd)),
                dict(Component='Fees',USD=str(record.fees_cash_usd)),dict(Component='Net futures/options result',USD=str(record.net_realized_usd))],
        cash_sweeps_excluded_usd=str(record.cash_sweeps_usd),candidate_only=True,publication_authorized=False,
        publication_blocker='Generation/report code is staged; futures publication still requires a complete writer and database rehearsal. Crypto must also reconcile.')


def verify_published(service,session,draft):
    start=date(draft.year,draft.month,1);end=date(draft.year,draft.month,monthrange(draft.year,draft.month)[1])
    record=load_published(session.artifacts,period_start=start,period_end=end)
    if record is None:return None
    expected=build_candidate(service,session,draft)
    if record.record_json!=expected.record_json:
        raise ValueError('Published futures financial record differs from current verified originals and owner evidence')
    return record
