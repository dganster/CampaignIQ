"""Strict local continuity must preserve ancestry or retain an explicit blocker."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import copy
import pytest

from campaigniq import historical_backfill_preflight as audit
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.forex_opening_statement import read_forex_opening_statement, attach_forex_opening
from campaigniq.import_contract import MonthlyInputRole as Role, monthly_import_contract
from campaigniq.import_preflight import MonthlyImportPreflight
from campaigniq.import_validation import MonthlyInputValidation
from campaigniq.monthly_import_execution import execute_monthly_import, MonthlyImportExecution
from campaigniq.closing_inventory_reconciliation import ClosingInventoryReconciliation
from campaigniq.persistence.lot_book_store import serialize_lot_book


def helpers():
    spec=spec_from_file_location('whole_year_test_helpers',Path(__file__).with_name('test_whole_year_backfill_preflight.py'))
    module=module_from_spec(spec);spec.loader.exec_module(module);return module


@pytest.fixture
def financial(tmp_path,monkeypatch):
    monkeypatch.delenv('RENDER',raising=False);monkeypatch.delenv('RENDER_SERVICE_ID',raising=False)
    root=Path(__file__).parent/'fixtures/forex_resolution'
    for suffix in ('monthly.txt','history.csv','report.csv'):
        (tmp_path/suffix).write_bytes((root/('aug-'+suffix)).read_bytes())
    end=date(2025,8,31)
    proof=read_forex_opening_statement((tmp_path/'monthly.txt').read_text(),trade_history=tmp_path/'history.csv',transaction_report=tmp_path/'report.csv',period_end=end)
    unknown=attach_forex_opening(LotBook(),proof,period_start=end.replace(day=1),authoritative=False)
    known=LotBook()
    for instrument in unknown.instruments():
        for lot in unknown.lots(instrument):known.seed(replace(lot,campaign_id='CARRIED:'+instrument.symbol))
    known._next_id=unknown._next_id
    empty=tmp_path/'empty.txt';empty.write_text('')
    supplied={Role.THINKORSWIM_TRADE_HISTORY:tmp_path/'history.csv',Role.SCHWAB_FOREX_TRANSACTION_REPORT:tmp_path/'report.csv',Role.SCHWAB_FOREX_MONTHLY_STATEMENT:tmp_path/'monthly.txt',Role.SCHWAB_REALIZED_GAIN_LOSS:empty,Role.SCHWAB_CLOSING_POSITION_SNAPSHOT:empty}
    contract=monthly_import_contract(2025,8)
    validations=tuple(MonthlyInputValidation(r.role,True,'fixture') for r in contract.requirements)
    return tmp_path,supplied,MonthlyImportPreflight(contract,validations,None,known,True,False),unknown


def test_real_monthly_preview_preserves_every_carried_identity_and_financial_control(financial):
    p,supplied,preflight,_=financial
    before=serialize_lot_book(period_end=date(2025,7,31),lot_book=preflight.opening_lot_book)
    sources={path:path.read_bytes() for path in supplied.values()}
    result=execute_monthly_import(preflight,authoritative_state_root=p/'state',supplied_inputs=supplied,preview_only=True,development_continuity=True)
    assert result.closing_reconciliation.reconciled and not result.closing_reconciliation.forex_source_findings
    assert result.forex_settlement_control_reconciled and result.forex_financing_control_reconciled
    assert serialize_lot_book(period_end=date(2025,7,31),lot_book=result.result.opening_lot_book)==before
    assert serialize_lot_book(period_end=date(2025,7,31),lot_book=preflight.opening_lot_book)==before
    assert sources=={path:path.read_bytes() for path in supplied.values()}
    assert not result.finalized and not (p/'state').exists()


def test_real_unknown_ancestry_is_not_invented_or_reassigned(financial):
    p,supplied,preflight,unknown=financial
    preflight=replace(preflight,bootstrap_opening_lot_book=unknown)
    before=serialize_lot_book(period_end=date(2025,7,31),lot_book=unknown)
    with pytest.raises(ValueError,match='unknown campaign ancestry'):
        execute_monthly_import(preflight,authoritative_state_root=p/'state',supplied_inputs=supplied,preview_only=True,development_continuity=True)
    assert serialize_lot_book(period_end=date(2025,7,31),lot_book=unknown)==before
    assert not (p/'state').exists()


@pytest.mark.parametrize('arguments',[
    dict(preview_only=False,development_continuity=True),
    dict(preview_only=True,development_continuity=1),
    dict(preview_only=True,development_continuity=True,historical_source_root='history'),
    dict(preview_only=True,development_continuity=True,reconciliation_decision=object()),
])
def test_unsafe_continuity_requests_fail_before_preflight_or_writes(tmp_path,arguments):
    with pytest.raises(ValueError):execute_monthly_import(None,authoritative_state_root=tmp_path/'state',supplied_inputs={},**arguments)
    assert not (tmp_path/'state').exists()


@pytest.mark.parametrize('key',['RENDER','RENDER_SERVICE_ID'])
def test_continuity_cannot_run_on_render(tmp_path,monkeypatch,key):
    monkeypatch.setenv(key,'present')
    with pytest.raises(ValueError,match='locally'):
        execute_monthly_import(None,authoritative_state_root=tmp_path/'state',supplied_inputs={},preview_only=True,development_continuity=True)
    assert not (tmp_path/'state').exists()


@pytest.fixture
def engine(monkeypatch):
    h=helpers();seen=[]
    def prepare(year,month,**kwargs):
        c=monthly_import_contract(year,month)
        with pytest.raises(RuntimeError):kwargs['artifact_storage'].write_text('2026-01-finalized.json','changed')
        return MonthlyImportPreflight(c,tuple(MonthlyInputValidation(r.role,True,'fixture') for r in c.requirements),None,LotBook())
    def execute(preflight,**kwargs):
        seen.append((preflight.contract.period_start.month,kwargs.get('development_continuity',False)))
        assert kwargs['preview_only'] and 'historical_source_root' not in kwargs and 'reconciliation_decision' not in kwargs
        return MonthlyImportExecution(h.result(preflight.opening_lot_book),ClosingInventoryReconciliation(()),None)
    monkeypatch.setattr(audit,'prepare_monthly_import',prepare)
    monkeypatch.setattr(audit,'execute_monthly_import',execute)
    return h,seen,execute


def test_preflight_enables_strict_mode_only_for_continuous_preview(engine):
    h,seen,_=engine;b=h.bundle(range(1,13));before=copy.deepcopy(b)
    report=audit.run_preflight(b,campaign_continuity=True)
    assert seen==[(1,False)]+[(m,strict) for m in range(2,13) for strict in (False,True)]
    assert report['identity_preserving_chain_verified'] and report['diagnostic_position_replay_completed']
    assert report['all_2025_drafts_passed']
    assert not report['publication_authorized'] and not report['full_portfolio_continuity_verified']
    assert b==before and report['captured_bytes_unchanged']
    assert all(r['exact_opening_state_carried'] for r in report['months'][1:])


def test_disabled_mode_preserves_existing_monthly_behavior(engine):
    h,seen,_=engine
    r=audit.run_preflight(h.bundle(range(1,13)))
    assert not r['campaign_continuity_enabled'] and not r['identity_preserving_chain_verified']
    assert all(not enabled for _,enabled in seen)


def test_strict_failure_remains_blocking_while_quantity_diagnostics_continue(engine,monkeypatch):
    h,seen,original=engine
    def execute(preflight,**kwargs):
        if preflight.contract.period_start.month==2 and kwargs.get('development_continuity'):
            raise ValueError('Unknown campaign ancestry; merging carried IDs is forbidden.')
        return original(preflight,**kwargs)
    monkeypatch.setattr(audit,'execute_monthly_import',execute)
    report=audit.run_preflight(h.bundle(range(1,13)),campaign_continuity=True)
    assert report['diagnostic_position_replay_completed'] and not report['identity_preserving_chain_verified']
    assert not report['all_2025_drafts_passed'] and not report['publication_authorized']
    feb=report['months'][1]['campaign_continuity'];march=report['months'][2]['campaign_continuity']
    assert feb['mode']=='legacy_quantity_fallback' and not feb['identity_preserving_replay_passed']
    assert 'Unknown campaign ancestry' in feb['blocking_findings'][0]
    assert march['mode']=='identity_preserving' and not march['connected_to_verified_identity_chain']


def test_hidden_opening_change_cannot_be_called_verified(engine,monkeypatch):
    h,_,original=engine
    def prepare(year,month,**kwargs):
        c=monthly_import_contract(year,month)
        return MonthlyImportPreflight(c,tuple(MonthlyInputValidation(r.role,True,'fixture') for r in c.requirements),None,h.book())
    def execute(preflight,**kwargs):
        r=original(preflight,**kwargs)
        if preflight.contract.period_start.month==2 and kwargs.get('development_continuity'):
            r.result.opening_lot_book=h.book(basis='101');r.result.ending_lot_book=h.book(basis='101')
        return r
    monkeypatch.setattr(audit,'prepare_monthly_import',prepare)
    monkeypatch.setattr(audit,'execute_monthly_import',execute)
    monkeypatch.setattr(audit,'capture_position_journal',lambda *a:[])
    r=audit.run_preflight(h.bundle(range(1,13)),campaign_continuity=True)
    assert not r['identity_preserving_chain_verified'] and not r['all_2025_drafts_passed']
    assert not r['months'][1]['campaign_continuity']['identity_preserving_replay_passed']
    assert r['months'][1]['opening_state_changes'][0]['field']=='basis_total'


def test_gap_never_restarts_a_verified_chain(engine):
    h,_,_=engine;r=audit.run_preflight(h.bundle([1,3,12]),campaign_continuity=True)
    assert not r['identity_preserving_chain_verified'] and not r['diagnostic_position_replay_completed']
    assert r['months'][2]['continuous_preview'] is None


@pytest.mark.parametrize('key',['RENDER','RENDER_SERVICE_ID'])
def test_preflight_refuses_render_in_strict_mode(engine,monkeypatch,key):
    h,_,_=engine;monkeypatch.setenv(key,'present')
    with pytest.raises(ValueError,match='locally'):audit.run_preflight(h.bundle(),campaign_continuity=True)


def test_mode_boolean_cannot_be_a_truthy_string(engine):
    h,_,_=engine
    with pytest.raises(ValueError,match='boolean'):audit.run_preflight(h.bundle(),campaign_continuity='yes')


def test_offline_runner_explicit_flag_is_recorded_without_remote_access(tmp_path,monkeypatch):
    import json
    from campaigniq import historical_backfill_runner as runner
    h=helpers();saved=tmp_path/'saved';runner.save_capture(h.bundle(),saved)
    monkeypatch.delenv('RENDER',raising=False);monkeypatch.delenv('RENDER_SERVICE_ID',raising=False)
    monkeypatch.setattr(runner.subprocess,'check_output',lambda cmd,**kw:'feature/historical-backfill-2025\n' if cmd[-1]=='--show-current' else 'fe5a516\n')
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**kw:pytest.fail('Offline runner attempted remote capture'))
    output=tmp_path/'report'
    monkeypatch.setattr('sys.argv',['runner','--repo',str(tmp_path/'repo'),'--development-only','--campaign-continuity','--capture',str(saved/'workspace-capture.json.gz'),'--output',str(output)])
    runner.main()
    report=json.loads((output/'preflight-report.json').read_text())
    assert report['campaign_continuity_enabled'] and not report['identity_preserving_chain_verified']
    assert not report['publication_authorized'] and report['captured_bytes_unchanged']
