from copy import deepcopy
from datetime import date
from decimal import Decimal as D
import json

import pytest

from campaigniq import account_futures_generation as module
from campaigniq.persistence.futures_month import parse_record,reference,load_published
from campaigniq.persistence.monthly_publication import ensure_publication_protocol_in_storage,publish_finalized_month_marker_to_storage
from campaigniq.persistence.realized_attribution_store import serialize_realized_attributions
from campaigniq.analytics.multi_month_analytics_summary import summarize_multi_month_analytics
from campaigniq.analytics.multi_month_performance_summary import summarize_multi_month_performance
from campaigniq.dashboard_sources import load_dashboard_sources
from tests.test_futures_monthly_connection import flat,connected,opening,saving,context,bound_context,end_confirm
from tests.test_account_local_publication import MemoryStorage


@pytest.fixture
def generated(flat):
    c=flat;end_confirm(c);return c


def record(c):
    repo=c.repositories[c.account]
    with c.associated.gateway.transaction(c.admin,c.account) as session:
        return module.build_candidate(c.associated,session,repo.get(c.selected.id))


def parse(text,r):return parse_record(text,account_id=r.account_id,draft_id=r.draft_id,period_start=r.period_start,period_end=r.period_end)


def staged(r):
    storage=MemoryStorage(r.account_id)
    ensure_publication_protocol_in_storage(storage,first_period_end=r.period_end)
    publish_finalized_month_marker_to_storage(storage,period_end=r.period_end)
    prefix=r.period_start.strftime('%Y-%m')+'-'
    storage.write_text(prefix+'futures.json',r.record_json)
    storage.write_text(prefix+'account-publication-receipt.json',json.dumps(dict(
        format='campaigniq.deployment_account_publication',version=1,account_id=r.account_id,draft_id=r.draft_id,
        draft_revision=r.source_revision,period_end=r.period_end.isoformat(),futures_generation=reference(r))))
    storage.write_text(prefix+'realized-attributions.json',serialize_realized_attributions(period_start=r.period_start,period_end=r.period_end,attributions=()))
    return storage


def test_candidate_roundtrip_replays_every_execution_and_preserves_all_sources(generated):
    c=generated;repo=c.repositories[c.account];before=deepcopy(repo.__dict__)
    r=record(c)
    assert r.net_realized_usd==D(-816) and r.option_realized_usd==200 and r.futures_realized_usd==-1000
    assert r.fees_cash_usd==-16 and r.cash_sweeps_usd==1000
    assert parse(r.record_json,r)==r
    assert len(r.to_dict()['fills'])==2 and len(r.to_dict()['deliveries'])==2
    assert record(c).record_json==r.record_json
    assert repo.__dict__==before
    assert '12345678' not in r.record_json and 'SYNTHETIC482' not in r.record_json


def test_generation_endpoint_is_readonly_and_returns_no_publication_authority(generated):
    c=generated;repo=c.repositories[c.account];before=deepcopy(repo.__dict__)
    result=c.associated.futures_generation_preview(c.admin,c.account,c.selected.id,repo.get(c.selected.id).revision)
    assert result['candidate_only'] and result['publication_authorized'] is False
    assert D(result['report'][-1]['USD'])==-816
    assert result['cash_sweeps_excluded_usd']=='1000.00'
    assert repo.__dict__==before
    with pytest.raises(ValueError):c.associated.futures_generation_preview(c.admin,c.account,c.selected.id,0)


def test_no_ending_assertion_cannot_build_financial_record(flat):
    with pytest.raises(ValueError,match='confirmations'):record(flat)


@pytest.mark.parametrize('change',['net','price','quantity','delivery_cash','missing_delivery','duplicate_fill','lineage','refs','profile','evidence','scope','revision','nan'])
def test_invalid_financial_record_never_loads(generated,change):
    r=record(generated);body=r.to_dict()
    if change=='net':body['financials']['net_realized_usd']='-815.00'
    elif change=='price':body['fills'][0]['price']='6'
    elif change=='quantity':body['fills'][0]['quantity']='-3'
    elif change=='delivery_cash':body['deliveries'][1]['reported_cash']='-999'
    elif change=='missing_delivery':body['deliveries']=[]
    elif change=='duplicate_fill':body['fills'].append(body['fills'][0]);body['source_lineage']['removed_futures_row_ids'].append(body['fills'][0]['source'])
    elif change=='lineage':body['source_lineage']['removed_futures_row_ids']=[]
    elif change=='refs':body['source_refs']['thinkorswim_trade_history']='bad'
    elif change=='profile':body['multiplier_usd']='100'
    elif change=='evidence':body['evidence']['independent_broker_position_verification']=True
    elif change=='scope':body['account_id']='other'
    elif change=='revision':body['source_revision']=True
    else:body['financials']['fees_cash_usd']='NaN'
    with pytest.raises(ValueError):parse(json.dumps(body),r)


def test_duplicate_json_fields_cannot_hide_financial_override(generated):
    r=record(generated)
    with pytest.raises(ValueError,match='Duplicate'):parse(r.record_json.rstrip()[:-1]+',"version": 1}',r)


def test_validated_record_and_descriptor_feed_existing_analytics_once(generated):
    r=record(generated);storage=staged(r);before=dict(storage.rows)
    summaries,_,_=load_dashboard_sources(storage)
    assert len(summaries)==1 and summaries[0].futures_applicable
    assert summaries[0].equity_options_realized_pnl==0 and summaries[0].forex_settled_pnl==0
    assert summaries[0].futures_realized_pnl==-816 and summaries[0].combined_realized_pnl==-816
    assert summarize_multi_month_performance(summaries).total_realized_pnl==-816
    assert load_published(storage,period_start=r.period_start,period_end=r.period_end)==r
    assert storage.rows==before


@pytest.mark.parametrize('case',['missing_marker','bad_marker','missing_receipt','missing_artifact','bad_descriptor','bad_revision','bad_source_refs','extra_period'])
def test_report_load_withholds_all_totals_on_record_or_receipt_conflict(generated,case):
    r=record(generated);storage=staged(r);prefix='2026-10-'
    if case=='missing_marker':del storage.rows[prefix+'finalized.json']
    elif case=='bad_marker':storage.rows[prefix+'finalized.json']='{}'
    elif case=='missing_receipt':del storage.rows[prefix+'account-publication-receipt.json']
    elif case=='missing_artifact':del storage.rows[prefix+'futures.json']
    elif case=='extra_period':storage.rows['2026-09-futures.json']=r.record_json
    else:
        receipt=json.loads(storage.rows[prefix+'account-publication-receipt.json'])
        if case=='bad_descriptor':receipt['futures_generation']['sha256']='0'*64
        elif case=='bad_source_refs':receipt['futures_generation']['source_refs']['thinkorswim_trade_history']='0'*64
        else:receipt['draft_revision']=True
        storage.rows[prefix+'account-publication-receipt.json']=json.dumps(receipt)
    before=dict(storage.rows)
    with pytest.raises((ValueError,FileNotFoundError,KeyError)):load_dashboard_sources(storage)
    assert storage.rows==before


def test_component_totals_are_exact_and_periods_are_not_silently_added():
    period=(date(2026,10,1),date(2026,10,31))
    summaries=summarize_multi_month_analytics(monthly_attributions={period:()},monthly_futures_pnl={period:D('-3793.60')})
    assert summaries[0].combined_realized_pnl==D('-3793.60')
    default=summarize_multi_month_analytics(monthly_attributions={period:()})[0]
    assert default.combined_realized_pnl==0 and not default.futures_applicable
    for value in (D('NaN'),1,1.0):
        with pytest.raises(ValueError):summarize_multi_month_analytics(monthly_attributions={period:()},monthly_futures_pnl={period:value})
    with pytest.raises(ValueError):summarize_multi_month_analytics(monthly_attributions={},monthly_futures_pnl={period:D(1)})


def test_published_component_is_recomputed_from_current_account_sources(generated,monkeypatch):
    c=generated;r=record(c);store=staged(r)
    with c.associated.gateway.transaction(c.admin,c.account) as session:
        session.artifacts=store;draft=session.drafts.get(c.selected.id)
        assert module.verify_published(c.associated,session,draft)==r
        actual=module.build_candidate
        monkeypatch.setattr(module,'build_candidate',lambda *a:__import__('dataclasses').replace(r,record_json=r.record_json+' '))
        with pytest.raises(ValueError,match='current verified'):module.verify_published(c.associated,session,draft)
