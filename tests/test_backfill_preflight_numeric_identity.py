"""Numeric equivalence must not conceal real provenance or financial changes."""
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, localcontext
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from campaigniq import historical_backfill_preflight as audit
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.import_preflight import MonthlyImportPreflight
from campaigniq.import_contract import monthly_import_contract
from campaigniq.import_validation import MonthlyInputValidation
from campaigniq.monthly_import_execution import MonthlyImportExecution
from campaigniq.closing_inventory_reconciliation import ClosingInventoryReconciliation
from campaigniq.persistence.position_journal import serialize_position_journal
from campaigniq.persistence.lot_book_store import serialize_lot_book

END=date(2025,1,31)


def book(strike='225',quantity='-5',basis='100',campaign='C1',lid='L1'):
    b=LotBook()
    b.seed(Lot(lid,OptionContract('AMZN',date(2025,3,21),Decimal(strike),OptionType.PUT),Decimal(quantity),datetime(2025,1,1),Decimal(basis),'broker',campaign))
    return b


def helpers():
    spec=spec_from_file_location('whole_year_helpers',Path(__file__).with_name('test_whole_year_backfill_preflight.py'))
    m=module_from_spec(spec);spec.loader.exec_module(m);return m


def test_equivalent_strikes_and_quantity_scale_do_not_create_positions():
    a=book();b=book('225.00','-5.0000','100.00')
    assert audit.quantities(a)==audit.quantities(b)
    assert not audit.differences(audit.quantities(a),audit.quantities(b))
    assert audit._state(a,END)==audit._state(b,END)
    assert audit._state_changes(a,b,END)==[]


@pytest.mark.parametrize('field,value',[('strike','226'),('quantity','-6'),('basis','101'),('campaign','C2'),('lid','L2')])
def test_real_changes_are_never_normalized_away(field,value):
    a=book();b=book(**{field:value})
    assert audit._state(a,END)!=audit._state(b,END)
    assert audit._state_changes(a,b,END)


def test_campaign_assignment_is_reported_with_prior_and_following_values():
    changes=audit._state_changes(book(campaign=None),book(campaign='2025-02:AMD:CAMP-000008'),END)
    assert changes==[dict(lot_id='L1',field='campaign_id',prior=None,following='2025-02:AMD:CAMP-000008')]


@pytest.mark.parametrize('value',['NaN','Infinity','-Infinity'])
def test_non_finite_numbers_are_rejected(value):
    with pytest.raises(ValueError):audit._number(value)


def test_numeric_canonicalization_does_not_round_to_decimal_context():
    with localcontext() as context:
        context.prec=3
        assert audit._number('123456789.123456789000')=='123456789.123456789'
        assert audit._number('-0.000')=='0'


def test_lot_order_does_not_change_identity_or_allocator():
    a=book();a.seed(Lot('L2',Instrument('META'),Decimal('1'),datetime(2025,1,1),None))
    b=LotBook()
    for instrument in reversed(a.instruments()):
        for lot in a.lots(instrument):b.seed(lot)
    b._next_id=a._next_id
    assert audit._state(a,END)==audit._state(b,END)
    b._next_id+=1
    assert audit._state_changes(a,b,END)==[dict(field='next_id',prior=a._next_id,following=b._next_id)]


def test_downstream_option_scale_uses_canonical_instrument_fields():
    h=helpers();b=h.bundle();r=h.result(book('225.00','-5.0000','100.00'))
    b['artifacts']['2026-01-position-journal.json']=serialize_position_journal(r,period_start=date(2026,1,1),period_end=date(2026,1,31))
    b['artifacts']['2026-01-lot-book.json']=serialize_lot_book(period_end=date(2026,1,31),lot_book=LotBook())
    row=audit.downstream(audit._FrozenStorage(b),book())[0]
    assert row['quantity_continuity_verified'] and row['identity_continuity_verified']
    assert not row['basis_continuity_verified']


def test_financial_finding_does_not_hide_later_diagnostic_replay(monkeypatch):
    h=helpers();seen=[]
    def prepare(year,month,**kwargs):
        contract=monthly_import_contract(year,month)
        return MonthlyImportPreflight(contract,tuple(MonthlyInputValidation(q.role,True,'fixture') for q in contract.requirements),None,LotBook())
    def execute(preflight,**kwargs):
        assert kwargs['preview_only'] and 'reconciliation_decision' not in kwargs
        seen.append(preflight.contract.period_start.month)
        return MonthlyImportExecution(h.result(),ClosingInventoryReconciliation(()),None)
    real=audit._execution_report
    def reported(execution,start):
        result=real(execution,start)
        if start.month==3:result['blocking_findings'].append('Forex monetary control failed: settlement')
        return result
    monkeypatch.setattr(audit,'prepare_monthly_import',prepare)
    monkeypatch.setattr(audit,'execute_monthly_import',execute)
    monkeypatch.setattr(audit,'_execution_report',reported)
    report=audit.run_preflight(h.bundle(range(1,13)))
    assert report['diagnostic_position_replay_completed']
    assert not report['all_2025_drafts_passed'] and not report['publication_authorized']
    assert report['months'][2]['blocking_findings'] and report['months'][11]['continuous_preview']
    assert seen[-1]==12


@pytest.mark.parametrize('name,month,total',[('aug',8,'-3040.50'),('sep',9,'-4638.00')])
def test_financial_report_retains_exact_control_values(tmp_path,name,month,total):
    spec=spec_from_file_location('financial_helpers',Path(__file__).with_name('test_forex_statement_resolution.py'))
    helper=module_from_spec(spec);spec.loader.exec_module(helper)
    source=Path(__file__).parent/'fixtures'/'forex_resolution'
    for suffix in ('monthly.txt','history.csv','report.csv'):
        (tmp_path/suffix).write_bytes((source/(name+'-'+suffix)).read_bytes())
    end=date(2025,month,31 if month==8 else 30)
    execution=helper.execute((name,tmp_path,end),True)
    row=audit._execution_report(execution,end.replace(day=1))
    assert row['forex_report_controls']['settlement_row_total_usd']==total
    assert row['forex_control_deltas_usd']['settlement']==('0.01' if month==8 else '0.00')
    assert not (tmp_path/'state').exists()
