"""Source-bound opening balances may explain observed closures, never old dates."""
import base64
import copy
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import importlib.util
from pathlib import Path
import pytest

from campaigniq.historical_backfill_origin_policy import bind_opening_origins, verify_opening_origins
from campaigniq.historical_backfill_opening_provenance import trace_opening_provenance
from campaigniq.domain.campaign import Campaign
from campaigniq.domain.campaign_continuity import plan_campaign_continuity
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.position_effect import PositionEffect as Effect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.monthly_import_execution import execute_monthly_import
from campaigniq.persistence.lot_book_store import serialize_lot_book


def helper(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


@pytest.fixture
def evidence():
    h=helper('test_backfill_opening_provenance')
    lid='SCHWAB-SNAPSHOT:2024-12-31T23:59:59.999999:EQUITY:ADBE'
    book=h.seed(h.snapshot(lid))
    raw=b'original saved opening statement'
    doc=dict(id='opening',role='schwab_opening_position_snapshot',sha256=sha256(raw).hexdigest(),content_base64=base64.b64encode(raw).decode())
    p=trace_opening_provenance(h.result(book),period_start=date(2025,1,1),period_end=date(2025,1,31),documents=[doc])
    return h,book,p['closing'],[doc]


def bind(fixture,**changes):
    _,book,receipts,docs=fixture
    return bind_opening_origins(changes.get('book',book),period_end=date(2025,1,31),receipts=changes.get('receipts',receipts),documents=changes.get('documents',docs))


def test_exact_snapshot_receipt_binds_without_assigning_a_campaign(evidence):
    _,book,receipts,_=evidence;before=serialize_lot_book(period_end=date(2025,1,31),lot_book=book)
    receipt=bind(evidence)
    assert receipt.documented_unknown_lot_ids==tuple(receipts)
    assert verify_opening_origins(receipt,book,period_end=date(2025,1,31))==tuple(receipts)
    assert before==serialize_lot_book(period_end=date(2025,1,31),lot_book=book)
    assert next(iter(receipts.values()))['actual_campaign_start'] is None


@pytest.mark.parametrize('change',['id','hash','role','missing'])
def test_changed_or_missing_source_receipt_is_rejected(evidence,change):
    docs=copy.deepcopy(evidence[3])
    if change=='id':docs[0]['id']='other'
    if change=='hash':docs[0]['sha256']='f'*64
    if change=='role':docs[0]['role']='unrelated'
    if change=='missing':docs=[]
    with pytest.raises(ValueError):bind(evidence,documents=docs)


@pytest.mark.parametrize('change',['quantity','date','campaign','basis'])
def test_changed_state_invalidates_bound_receipt(evidence,change):
    h,book,_,_=evidence;proof=bind(evidence);lot=book.lots(h.STOCK)[0]
    changes={'quantity':dict(quantity=Decimal(400)),'date':dict(opened_at=datetime(2024,1,1)),
             'campaign':dict(campaign_id='INVENTED'),'basis':dict(basis_total=Decimal(1))}[change]
    other=h.seed(replace(lot,**changes))
    with pytest.raises(ValueError,match='differs'):verify_opening_origins(proof,other,period_end=date(2025,1,31))


def test_wrong_boundary_is_rejected(evidence):
    with pytest.raises(ValueError,match='boundary'):
        verify_opening_origins(bind(evidence),evidence[1],period_end=date(2025,2,28))


@pytest.mark.parametrize('key',['RENDER','RENDER_SERVICE_ID'])
def test_origin_binding_cannot_run_on_render(evidence,monkeypatch,key):
    monkeypatch.setenv(key,'present')
    with pytest.raises(ValueError,match='local development'):bind(evidence)


@pytest.mark.parametrize('kind',['changed_carried_identity','observed_open'])
def test_other_receipt_kinds_cannot_approve_an_unknown_root(evidence,kind):
    receipts=copy.deepcopy(evidence[2]);next(iter(receipts.values()))['origin_kind']=kind
    assert not bind(evidence,receipts=receipts).documented_unknown_lot_ids


def test_receipt_quantity_must_match_exactly(evidence):
    receipts=copy.deepcopy(evidence[2]);next(iter(receipts.values()))['quantity']='499'
    assert not bind(evidence,receipts=receipts).documented_unknown_lot_ids


def test_later_snapshot_cannot_reset_initial_origin(evidence):
    receipts=copy.deepcopy(evidence[2]);next(iter(receipts.values()))['snapshot_observed_at']='2025-01-31'
    assert not bind(evidence,receipts=receipts).documented_unknown_lot_ids


def test_proof_is_unusable_outside_preview_before_any_database_access(tmp_path,evidence):
    with pytest.raises(ValueError,match='requires development'):
        execute_monthly_import(None,authoritative_state_root=tmp_path,supplied_inputs={},development_opening_origins=bind(evidence))
    with pytest.raises(ValueError,match='preview only'):
        execute_monthly_import(None,authoritative_state_root=tmp_path,supplied_inputs={},development_opening_origins=bind(evidence),development_continuity=True)
    assert not list(tmp_path.iterdir())


def connected_plan(*,documented=True,mixed=False,anchor=True):
    h=helper('test_trade_level_campaign_linkage');book=h.opening(stock=True)
    legs=[h.leg(h.STOCK,Side.SELL,Effect.CLOSE,'500')]
    if anchor:legs.append(h.leg(h.CALL,Side.BUY,Effect.CLOSE,'5'))
    if mixed:legs.append(h.leg(h.PUT,Side.BUY,Effect.CLOSE,'5'))
    trade=Trade(tuple(legs));history=PositionHistory();history.add_trade(trade)
    before=serialize_lot_book(period_end=date(2025,1,31),lot_book=book)
    p=plan_campaign_continuity(opening_lot_book=book,position_history=history,campaigns=(Campaign(h.NEW,(trade,),True),),
        documented_opening_lots=('unknown-shares',) if documented else ())
    assert before==serialize_lot_book(period_end=date(2025,1,31),lot_book=book)
    return p,h


def test_documented_shares_can_support_one_observed_carried_connection():
    p,h=connected_plan()
    assert not p.blocking_findings and p.campaigns[0].campaign_id==h.OLD_CALL
    assert p.trade_bindings[0].documented_opening_lot_ids==('unknown-shares',)
    assert p.trade_bindings[0].evidence_kind=='documented_opening_fifo'
    assert p.campaigns[0].started_before_data


def test_default_still_blocks_undocumented_unknown_shares():
    p,_=connected_plan(documented=False)
    assert p.blocking_findings and not p.trade_bindings[0].supported_by_fifo


def test_opening_balance_alone_cannot_invent_a_campaign_connection():
    p,_=connected_plan(anchor=False)
    assert any('exactly one' in finding for finding in p.blocking_findings)
    assert not p.trade_bindings[0].supported_by_fifo


def test_documented_origin_does_not_allow_two_known_campaigns_to_merge():
    p,_=connected_plan(mixed=True)
    assert any('distinct carried campaigns' in finding for finding in p.blocking_findings)
    assert not p.trade_bindings[0].supported_by_fifo


def test_observed_fifo_does_not_clear_a_campaign_start_history_requirement():
    h=helper('test_whole_year_backfill_preflight')
    from campaigniq import historical_backfill_preflight as audit
    from campaigniq.domain.boundary_reconstruction import BoundaryReconstruction
    from campaigniq.domain.boundary_validation import HistoricalRequirement
    from campaigniq.monthly_import_execution import MonthlyImportExecution
    from campaigniq.closing_inventory_reconciliation import ClosingInventoryReconciliation
    r=h.result();r.observed_fifo_boundary_cases=('CARRIED',)
    req=HistoricalRequirement(case_id='CARRIED',earliest_unresolved_date=date(2024,12,31),months=('2024-12',),
        document_types=('Account Trade History',),reason='The opening position is known, but the supplied history does not establish when the campaign began.')
    r.boundary_reconstruction=BoundaryReconstruction(('stock',),('CARRIED',),(req,))
    report=audit._execution_report(MonthlyImportExecution(r,ClosingInventoryReconciliation(()),None),date(2025,1,1))
    assert report['blocking_findings'] and not report['observed_fifo_boundary_cases']
    assert not report['original_campaign_start_dates_verified']


def test_assignment_origin_requires_exact_share_to_option_delivery(evidence):
    h,_,_,docs=evidence
    from campaigniq.domain.position_event import PositionEvent,PositionChange
    from campaigniq.domain.position_event_kind import PositionEventKind
    lid='SCHWAB-SNAPSHOT:2024-12-31T23:59:59.999999:OPTION:ADBE:2025-02-21:PUT:500'
    event=PositionEvent(kind=PositionEventKind.ASSIGNMENT,occurred_at=datetime(2025,1,8),
        changes=(PositionChange(h.PUT,Decimal(5)),PositionChange(h.STOCK,Decimal(500))))
    statement=dict(docs[0],id='statement',role='schwab_brokerage_statement')
    r=h.result(h.seed(h.snapshot(lid,h.PUT,'-5')),events=(event,))
    p=trace_opening_provenance(r,period_start=date(2025,1,1),period_end=date(2025,1,31),documents=docs+[statement])
    proof=bind_opening_origins(r.ending_lot_book,period_end=date(2025,1,31),receipts=p['closing'],documents=docs+[statement])
    assert proof.documented_unknown_lot_ids==tuple(p['closing'])
    bad=copy.deepcopy(p['closing']);next(iter(bad.values()))['parent_consumed_quantities'][lid]='4'
    proof=bind_opening_origins(r.ending_lot_book,period_end=date(2025,1,31),receipts=bad,documents=docs+[statement])
    assert not proof.documented_unknown_lot_ids
