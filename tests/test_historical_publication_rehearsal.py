"""Local admission guards and economic candidate generation must fail closed."""
from dataclasses import replace
from datetime import date,datetime
from decimal import Decimal as D
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

from campaigniq.historical_backfill_rehearsal import local_parameters,schema_name,command_environment,SchemaStorage,tools_for_server
from campaigniq.historical_backfill_realized_replay import ChronologicalRealizedLotAttributor
from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
from campaigniq.domain.campaign import Campaign
from campaigniq.domain.position_event import PositionEvent,PositionChange
from campaigniq.domain.position_event_kind import PositionEventKind as Kind
from campaigniq.domain.position_effect import PositionEffect as Effect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.historical_backfill_candidate_payloads import CandidateCollector
from campaigniq.persistence.lot_book_store import serialize_lot_book


def helpers():
    spec=importlib.util.spec_from_file_location('origin_helpers',Path(__file__).with_name('test_backfill_opening_provenance.py'))
    h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h);return h


@pytest.mark.parametrize('dsn',[
    'postgresql:///campaigniq_production_db?host=/var/run/postgresql',
    'postgresql://example.com/campaigniq_dev',
    'dbname=campaigniq_dev host=127.0.0.1 hostaddr=8.8.8.8',
    'dbname=campaigniq_dev host=127.0.0.1 options=-csearch_path=public',
    'dbname=campaigniq_dev host=localhost,example.com',
    'dbname=campaigniq_dev host=/tmp',
    'dbname=campaigniq_dev service=production',
    'dbname=campaigniq_dev',
])
def test_remote_ambiguous_or_production_connections_are_rejected(dsn):
    with pytest.raises(ValueError):local_parameters(dsn)


@pytest.mark.parametrize('host',['/var/run/postgresql','localhost','127.0.0.1','::1'])
def test_only_explicit_local_dev_connections_are_accepted(host):
    p=local_parameters('dbname=campaigniq_dev host='+host)
    assert p['dbname']=='campaigniq_dev' and p['port']=='5432'


@pytest.mark.parametrize('key',['RENDER','RENDER_SERVICE_ID'])
def test_render_execution_is_refused(monkeypatch,key):
    monkeypatch.setenv(key,'present')
    with pytest.raises(ValueError,match='refuses Render'):
        local_parameters('dbname=campaigniq_dev host=/var/run/postgresql')


@pytest.mark.parametrize('name',['public','campaigniq_dev','ciq_rehearsal_bad','ciq_rehearsal_0123456789ab; DROP SCHEMA public','ciq_rehearsal_0123456789ab_other'])
def test_arbitrary_schemas_cannot_be_selected(name):
    with pytest.raises(ValueError):schema_name(name)


def test_generated_schema_and_retained_copies_are_accepted():
    for suffix in ('','_baseline','_staged'):
        assert schema_name('ciq_rehearsal_0123456789ab'+suffix)


def test_database_child_environment_does_not_inherit_service_or_remote_routing(monkeypatch):
    monkeypatch.setenv('PGSERVICE','production');monkeypatch.setenv('PGHOSTADDR','8.8.8.8');monkeypatch.setenv('PGPORT','6543')
    p=local_parameters('dbname=campaigniq_dev host=/var/run/postgresql')
    env=command_environment(p)
    assert 'PGSERVICE' not in env and 'PGHOSTADDR' not in env
    assert env['PGPORT']=='5432' and env['PGDATABASE']=='campaigniq_dev'


@pytest.mark.parametrize('key',['2026-01-lot-book.json','2025-13-lot-book.json','../2025-01-lot-book.json','.campaigniq-monthly-publication-v1.json','2024-12-lot-book.json'])
def test_candidate_insert_guard_blocks_other_periods_and_protocol_before_sql(key):
    class Connection:
        def execute(self,*args):raise AssertionError('SQL must not run')
    store=SchemaStorage(Connection(),'ciq_rehearsal_0123456789ab','workspace-dennis')
    with pytest.raises(ValueError):store.insert_candidate(key,'{}')


def test_tool_major_version_must_match_local_server(monkeypatch):
    import campaigniq.historical_backfill_rehearsal as module
    monkeypatch.setattr(module.shutil,'which',lambda name:'/fake/'+name)
    monkeypatch.setattr(Path,'is_file',lambda p:False)
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout='pg_dump (PostgreSQL) 18.6'))
    with pytest.raises(ValueError,match='LOCAL server'):tools_for_server(16)
    assert tools_for_server(18)['pg_dump']=='/fake/pg_dump'


def record(instrument,qty,day,basis='100'):
    return RealizedGainLossRecord(closed_date=day,instrument=instrument,quantity=D(qty),closing_price=D(1),
        proceeds=D(basis),cost_basis=D(basis),gain_loss=D(0),basis_method='FIFO',term='SHORT',disallowed_loss=D(0))


def test_assignment_delivery_is_applied_before_later_share_sale():
    h=helpers();book=h.seed(h.snapshot('put',h.PUT,'-5','PUT-ORIGIN'))
    event=PositionEvent(kind=Kind.ASSIGNMENT,occurred_at=datetime(2025,1,8),
        changes=(PositionChange(h.PUT,D(5)),PositionChange(h.STOCK,D(500))))
    sale=Trade((h.leg(h.STOCK,qty='500',side=Side.SELL,effect=Effect.CLOSE),))
    broker=record(h.STOCK,'500',date(2025,1,22))
    before=serialize_lot_book(period_end=date(2024,12,31),lot_book=book)
    attrs=ChronologicalRealizedLotAttributor(book.clone()).attribute_campaigns([Campaign('SALE',(sale,))],[broker],[event])
    assert attrs[0].record==broker and attrs[0].basis_reconciled and attrs[0].allocated_quantity==D(500)
    assert before==serialize_lot_book(period_end=date(2024,12,31),lot_book=book)
    with pytest.raises(ValueError):RealizedLotAttributor(book.clone()).attribute_campaigns([Campaign('SALE',(sale,))],[broker],[event])


def test_call_reentry_then_expiration_does_not_disappear_because_prior_trade_closed_same_contract():
    h=helpers();book=h.seed(h.snapshot('put',h.PUT,'-10','ORIGIN'))
    closed=Trade((h.leg(h.PUT,qty='10',side=Side.BUY,effect=Effect.CLOSE),))
    opened_leg=h.leg(h.PUT,qty='10');opened_leg=replace(opened_leg,executions=tuple(replace(e,executed_at=datetime(2025,1,23)) for e in opened_leg.executions))
    opened=Trade((opened_leg,));event=PositionEvent(kind=Kind.EXPIRATION,occurred_at=datetime(2025,1,24),changes=(PositionChange(h.PUT,D(10)),))
    records=[record(h.PUT,'10',date(2025,1,22)),record(h.PUT,'10',date(2025,1,24))]
    attrs=ChronologicalRealizedLotAttributor(book).attribute_campaigns([Campaign('C',(closed,opened))],records,[event])
    assert len(attrs)==2 and all(a.record==r and a.basis_reconciled for a,r in zip(attrs,records))


@pytest.mark.parametrize('trade_day,settlement',[(date(2025,10,10),date(2025,10,14)),(date(2025,11,10),date(2025,11,12))])
def test_occ_2025_banking_holidays_use_exact_settlement_dates(trade_day,settlement):
    assert ChronologicalRealizedLotAttributor._next_business_day(trade_day)==settlement
    assert RealizedLotAttributor._next_business_day(trade_day)!=settlement


def test_other_year_is_not_given_a_new_date_waiver():
    assert ChronologicalRealizedLotAttributor._next_business_day(date(2026,11,10))==RealizedLotAttributor._next_business_day(date(2026,11,10))


def test_contract_alias_keeps_original_broker_record_and_exact_contract_checks():
    h=helpers();target=replace(h.PUT,underlying='SPX');source=replace(target,underlying='SPXW')
    book=h.seed(h.snapshot('index',target,'-5','INDEX'))
    trade=Trade((h.leg(target,qty='5',side=Side.BUY,effect=Effect.CLOSE),))
    broker=record(source,'5',date(2025,1,22))
    attrs=ChronologicalRealizedLotAttributor(book.clone(),contract_aliases={source:target}).attribute_campaigns([Campaign('C',(trade,))],[broker],[])
    assert attrs[0].record.instrument.underlying=='SPXW' and attrs[0].record==broker
    with pytest.raises(ValueError):ChronologicalRealizedLotAttributor(book.clone(),contract_aliases={source:replace(target,strike=D(999))})


def test_candidate_collector_never_accepts_finalized_results():
    collector=CandidateCollector({})
    with pytest.raises(ValueError,match='unfinalized'):
        collector.collect(None,SimpleNamespace(finalized=True),{})
    assert not collector.artifacts


@pytest.fixture
def complete_generation():
    spec=importlib.util.spec_from_file_location('monthly_helpers',Path(__file__).with_name('test_whole_year_backfill_preflight.py'))
    h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
    from campaigniq.import_contract import monthly_import_contract
    from campaigniq.monthly_import_execution import MonthlyImportExecution
    from campaigniq.closing_inventory_reconciliation import ClosingInventoryReconciliation
    collector=CandidateCollector({})
    for month in range(1,13):
        result=h.result();result.realized_gain_loss=();result.attribution_events=();result.lifecycle_transitions=()
        result.forex_settlement_attributions=();result.crypto_report=None
        preflight=SimpleNamespace(contract=monthly_import_contract(2025,month),forex_applicable=False,crypto_applicable=False)
        collector.collect(preflight,MonthlyImportExecution(result,ClosingInventoryReconciliation(()),None),{})
    return collector.artifacts


def test_all_twelve_candidate_generations_roundtrip_existing_readers(complete_generation):
    from campaigniq.historical_backfill_candidate_payloads import validate_generation
    assert len(complete_generation)==108 and validate_generation(complete_generation)


@pytest.mark.parametrize('change',['missing','wrong_period','future','unknown_family','nan'])
def test_incomplete_or_malformed_generations_are_rejected_before_database_writes(complete_generation,change):
    import json
    from campaigniq.historical_backfill_candidate_payloads import validate_generation
    if change=='missing':del complete_generation['2025-01-finalized.json']
    if change=='wrong_period':
        key='2025-01-lot-book.json';obj=json.loads(complete_generation[key]);obj['period_end']='2026-01-31';complete_generation[key]=json.dumps(obj)
    if change=='future':complete_generation['2026-01-lot-book.json']='{}'
    if change=='unknown_family':complete_generation['2025-01-unexpected.json']='{}'
    if change=='nan':complete_generation['2025-01-finalized.json']=complete_generation['2025-01-finalized.json'].replace('"version": 1','"version": NaN')
    with pytest.raises(ValueError):validate_generation(complete_generation)


def test_server_identity_is_checked_before_any_schema_write(monkeypatch):
    from campaigniq.historical_backfill_rehearsal import _connect
    import psycopg
    class Connection:
        closed=False
        def execute(self,query):
            assert query=='SELECT current_database(),inet_server_addr()::text'
            return SimpleNamespace(fetchone=lambda:('campaigniq_production_db','127.0.0.1'))
        def close(self):self.closed=True
    c=Connection();monkeypatch.setattr(psycopg,'connect',lambda **kw:c)
    with pytest.raises(ValueError,match='Server identity'):_connect(dict(host='/var/run/postgresql',dbname='campaigniq_dev'))
    assert c.closed


def test_remote_parameters_are_refused_before_connecting(monkeypatch):
    from campaigniq.historical_backfill_rehearsal import _connect
    import psycopg
    monkeypatch.setattr(psycopg,'connect',lambda **kw:(_ for _ in ()).throw(AssertionError('Network must not run')))
    with pytest.raises(ValueError):_connect(dict(host='example.com',dbname='campaigniq_dev'))
