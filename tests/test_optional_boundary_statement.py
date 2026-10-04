import ast
import hashlib
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from decimal import Decimal
import pytest

from campaigniq.import_contract import MonthlyInputRole, monthly_import_contract
from campaigniq.monthly_import_execution import _validated_next_month_assignment_lines
from campaigniq.importers.schwab.option_assignment_section_reader import read_option_assignment_section


def lines():
    return [
        '07/01 Sale                                   APD                  AIR PRODS & CHEMS INC                                        (100.0000)             270.0000',
        '                                                                  Trade Date: 06/30/26 / Industry Fee $0.58',
        '        Other         Option                 APD 07/17/2026 CALL AIR PRODS & CHEMS I$270                                           1.0000',
        '        Activity      Assignment             270.00 C       EXP 07/17/26',
        '07/02 Sale                                   AXP                  AMERICAN EXPRESS CO                                          (100.0000)             300.0000',
    ]


def test_inline_trade_date_and_fee_preserve_both_dates():
    row, = read_option_assignment_section(lines())
    assert row.transaction_date == date(2026,7,1)
    assert row.trade_date == date(2026,6,30)
    assert row.expiration == date(2026,7,17)
    assert row.strike == Decimal('270')
    assert row.quantity == 1


def test_statement_is_optional_not_a_required_monthly_input():
    contract = monthly_import_contract(2026,6)
    assert MonthlyInputRole.SCHWAB_NEXT_MONTH_ASSIGNMENT_EVIDENCE not in {r.role for r in contract.requirements if r.required}


def test_wrong_statement_month_is_rejected(tmp_path):
    path=tmp_path/'wrong.txt';path.write_text('June 1-30, 2026\n')
    with pytest.raises(ValueError,match='Next-month assignment statement'):
        _validated_next_month_assignment_lines(path,period_end=date(2026,6,30))


def test_boundary_statement_validation_uses_next_month(tmp_path,monkeypatch):
    import campaigniq.import_validation as validation
    path=tmp_path/'July.txt';path.write_text('\n'.join(lines()))
    observed=[]
    def validate(role,source,**kwargs):
        observed.append((role,source,kwargs))
        return SimpleNamespace(valid=True)
    monkeypatch.setattr(validation,'validate_monthly_input',validate)
    assert _validated_next_month_assignment_lines(path,period_end=date(2026,6,30)) == lines()
    assert observed == [(MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT,path,dict(period_start=date(2026,7,1),period_end=date(2026,7,31)))]


def test_later_next_month_assignment_is_not_boundary_evidence(tmp_path,monkeypatch):
    import campaigniq.import_validation as validation
    monkeypatch.setattr(validation,'validate_monthly_input',lambda *args,**kwargs:SimpleNamespace(valid=True))
    path=tmp_path/'July.txt';path.write_text('\n'.join(s.replace('07/01 Sale','07/02 Sale') for s in lines()))
    with pytest.raises(ValueError,match='first-business-day'):
        _validated_next_month_assignment_lines(path,period_end=date(2026,6,30))


def dashboard_functions():
    tree=ast.parse(Path('src/campaigniq/ui/dashboard.py').read_text())
    selected=[node for node in tree.body if
        isinstance(node,ast.FunctionDef) and node.name in {'_monthly_import_signature','_save_uploaded_monthly_inputs'}
        or isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='MONTHLY_UPLOAD_ROLES' for t in node.targets)]
    env=dict(Path=Path,MonthlyInputRole=MonthlyInputRole,hashlib=hashlib)
    exec(compile(ast.Module(body=selected,type_ignores=[]),'dashboard helpers','exec'),env)
    return env


def test_optional_upload_changes_validated_signature():
    env=dashboard_functions();signature=env['_monthly_import_signature']
    upload=SimpleNamespace(name='July.txt',getvalue=lambda:b'July evidence')
    before=signature(year=2026,month=6,uploads={})
    after=signature(year=2026,month=6,uploads={MonthlyInputRole.SCHWAB_NEXT_MONTH_ASSIGNMENT_EVIDENCE:upload})
    assert before != after


def test_optional_statement_is_saved_with_its_own_provenance_role(tmp_path):
    env=dashboard_functions()
    upload=SimpleNamespace(name='July.txt',getvalue=lambda:b'July evidence')
    saved=env['_save_uploaded_monthly_inputs'](upload_dir=tmp_path,uploads={MonthlyInputRole.SCHWAB_NEXT_MONTH_ASSIGNMENT_EVIDENCE:upload})
    assert set(saved)=={MonthlyInputRole.SCHWAB_NEXT_MONTH_ASSIGNMENT_EVIDENCE}
    assert saved[MonthlyInputRole.SCHWAB_NEXT_MONTH_ASSIGNMENT_EVIDENCE].read_bytes()==b'July evidence'


def test_full_pipeline_matches_june_realized_close_without_importing_july_positions(tmp_path):
    from campaigniq.import_pipeline import PeriodImportPipeline
    from campaigniq.domain.lot_book import LotBook
    from campaigniq.domain.lot import Lot
    from campaigniq.domain.value_objects.instrument import Instrument
    from campaigniq.importers.schwab.option_assignment_flow import read_option_assignment_events
    from campaigniq.analytics.period_realized_attributions import attribute_period_realized_pnl
    from campaigniq.persistence.position_journal import serialize_position_journal
    path=tmp_path/'June.csv'
    path.write_text('Account Statement\n\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n7/1/26,04:21:18,EXP,1,SOLD -100.0 APD UPON AIR PRODS & CHEMS INC,-0.58,,27000,0\n\nAccount Trade History\n,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type\n\nForex Statements\nTrade Date,Exec Date,Exec Time,Type,Ref #,Description,Commissions & Fees,Amount,Balance\n\n')
    gain_loss=tmp_path/'realized.txt'
    gain_loss.write_text('APD  06/30/2026  100  $283.74  FIFO  $28,373.73  $29,349.00  -$975.27  -$975.27\n')
    book=LotBook();event,=read_option_assignment_events(lines())
    for instrument,quantity in [(Instrument('APD'),Decimal('100')),(event.changes[0].instrument,Decimal('-1'))]:
        book.seed(Lot(str(instrument),instrument,quantity,datetime(2026,5,1),None))
    result=PeriodImportPipeline().run(period_start=date(2026,6,1),period_end=date(2026,6,30),thinkorswim_trade_history=path,carried_opening_lot_book=book,boundary_assignment_lines=(lines(),),realized_gain_loss_report=gain_loss)
    assert len(result.realized_gain_loss)==1
    attributed,=attribute_period_realized_pnl(result)
    assert attributed.record.closed_date==date(2026,6,30)
    assert attributed.record.gain_loss==Decimal('-975.27')
    assert result.position_events==()
    assert sum(l.quantity for l in result.ending_lot_book.lots(Instrument('APD')))==100
    assert sum(l.quantity for l in result.ending_lot_book.lots(event.changes[0].instrument))==-1
    serialize_position_journal(result,period_start=date(2026,6,1),period_end=date(2026,6,30))
