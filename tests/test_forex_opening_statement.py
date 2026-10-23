from datetime import date, datetime
from decimal import Decimal
from dataclasses import replace
from pathlib import Path
from uuid import uuid4
import pytest

from campaigniq.forex_opening_statement import (
    read_forex_opening_statement, attach_forex_opening, verify_forex_ending,
)
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.value_objects.forex_pair import ForexPair
from campaigniq.import_contract import MonthlyInputRole as Role
from campaigniq.import_pipeline import PeriodImportPipeline

SUMMARY = '''Customer Account Summary Report since Friday, November 28, 2025 16:00:00 (CST) through Wednesday, December 31, 2025 16:00:00 (CST)
Monthly Equity Summary
Account Account Currency
1234567 USD
Open Position(s) at End of Month
Currency Pair Buy/Sell Quantity Trade Price
Floating Profit/Loss Net USD :
'''
FIRST = '''Customer Account Summary Report since Friday, November 28, 2025 16:00:00 (CST) through Monday, December 1, 2025 16:00:00 (CST)
Open Position(s)
USD/MXN
TR Details: 11/24/2025 2:13:41 PM Buy 50,000 18.5177400
USD/MXN - TOTAL POSITION: Buy 50,000 ($599.13) $5,000
Closed Position(s)
Trade Activity Detail
Financing Report
'''
LAST = '''Customer Account Summary Report since Thursday, December 4, 2025 16:00:00 (CST) through Friday, December 5, 2025 16:00:00 (CST)
Trade Activity Detail
2025-12-05 10:30:21 USD/MXN STOP S CLOSE (50,000) 18.15063 1004840489728 $13.75
Financing Report
'''
TEXT = '\f'.join((SUMMARY,FIRST,LAST))

@pytest.fixture
def files(tmp_path):
    tos=tmp_path/'history.csv'
    tos.write_text('Account Statement test\n\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n\nAccount Trade History\nNotes,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type\n\nForex Statements\n,Date,Time,Type,Ref #,Description,Commissions & Fees,Amount,Amount(USD),Balance\n,12/5/25,08:30:21,TRD,="1004840489728","SOLD -50,000 USD/MXN @18.15063",--,--,($1008.60),$5435.78\n\n')
    report=tmp_path/'forex.csv'
    report.write_text('Transaction Report since Nov 30, 2025 through Dec 31, 2025\n"Account (USD): 1234567, Base currency: (USD)"\nMTD Settled PL,$0.00\nMTD Fee,$0.00\n')
    return tos,report


def read(files,text=TEXT):
    return read_forex_opening_statement(text,trade_history=files[0],transaction_report=files[1],period_end=date(2025,12,31))


def test_independent_opening_and_fills_reconcile_without_modifying_sources(files):
    before=[p.read_bytes() for p in files]
    e=read(files)
    assert e.opening==(('USD/MXN',Decimal(50000)),)
    assert e.closing==()
    assert all(len(x)==64 for x in (e.source_sha256,e.history_sha256,e.report_sha256))
    assert before==[p.read_bytes() for p in files]


def test_real_pipeline_closes_carried_forex_lot(files):
    e=read(files)
    original=LotBook()
    book=attach_forex_opening(original,e,period_start=date(2025,12,1),authoritative=False)
    r=PeriodImportPipeline().run(period_start=date(2025,12,1),period_end=date(2025,12,31),thinkorswim_trade_history=files[0],carried_opening_lot_book=book,forex_transaction_report=files[1],campaign_namespace='2025-12')
    assert not r.ending_lot_book.instruments()
    verify_forex_ending(r.ending_lot_book,e)
    assert not original.instruments()
    assert book.lots(ForexPair.from_symbol('USD/MXN'))[0].quantity==Decimal(50000)
    assert r.forex_transaction_report.settlement_pl_usd==0
    assert r.forex_transaction_report.settlements==()  # Missing CSV close is not repaired.

@pytest.mark.parametrize('old,new',[
    ('1234567 USD','7654321 USD'),
    ('December 31, 2025','November 30, 2025'),
    ('(CST)','(EST)'),
    ('Monday, December 1, 2025','Tuesday, December 2, 2025'),
    ('Buy 50,000 ($599.13)','Buy 50,001 ($599.13)'),
    ('Buy 50,000 ($599.13)','Sell 50,000 ($599.13)'),
    ('TR Details: 11/24/2025','TR Details: 12/01/2025'),
    ('TR Details: 11/24/2025','Missing detail:'),
    ('CLOSE (50,000) 18.15063','CLOSE (50,000) 18.15064'),
    ('S CLOSE (50,000)','B CLOSE (50,000)'),
    ('S CLOSE (50,000)','S OPEN (50,000)'),
    ('1004840489728 $13.75','1004840489729 $13.75'),
    ('Open Position(s) at End of Month','Unknown closing section'),
    ('Floating Profit/Loss Net USD :','Unknown footer'),
    ('Closed Position(s)','Missing daily closing section'),
])
def test_conflicting_or_incomplete_evidence_fails_closed(files,old,new):
    with pytest.raises(ValueError): read(files,TEXT.replace(old,new))


def test_duplicate_monthly_execution_rejected(files):
    with pytest.raises(ValueError,match='Duplicate'):
        read(files,TEXT+'\f'+LAST)


def test_duplicate_history_execution_rejected(files):
    line=files[0].read_text().splitlines()[-2]
    with files[0].open('a') as f:f.write(line+'\n\n')
    with pytest.raises(ValueError,match='Duplicate'):
        read(files)


def test_unrecognized_monthly_execution_rejected(files):
    with pytest.raises(ValueError,match='Unrecognized'):
        read(files,TEXT.replace('18.15063 1004840489728','bad 1004840489728'))


def test_cannot_use_snapshot_after_early_execution(files):
    files[0].write_text(files[0].read_text().replace('12/5/25','12/1/25'))
    with pytest.raises(ValueError,match='follow executions'): read(files)


def test_cannot_double_seed_a_wider_history(files):
    files[0].write_text(files[0].read_text().replace('12/5/25','11/24/25'))
    with pytest.raises(ValueError,match='monthly-only'): read(files)


def test_wrong_month_end_rejected(files):
    with pytest.raises(ValueError,match='complete month'):
        read_forex_opening_statement(TEXT,trade_history=files[0],transaction_report=files[1],period_end=date(2025,12,30))


def book_with_position(quantity='50000'):
    book=LotBook()
    book.seed(Lot('canonical-lot',ForexPair.from_symbol('USD/MXN'),Decimal(quantity),datetime(2025,11,24),None,campaign_id='canonical-campaign'))
    return book


def test_authoritative_lot_identity_preserved(files):
    original=book_with_position();e=read(files)
    clone=attach_forex_opening(original,e,period_start=date(2025,12,1),authoritative=True)
    assert clone is not original
    i=ForexPair.from_symbol('USD/MXN')
    assert clone.lots(i)==original.lots(i)
    assert clone.lots(i)[0].lot_id=='canonical-lot'
    assert clone.lots(i)[0].campaign_id=='canonical-campaign'

@pytest.mark.parametrize('authoritative',[True,False])
def test_existing_quantities_are_not_overwritten(files,authoritative):
    original=book_with_position('40000')
    with pytest.raises(ValueError,match='existing opening state'):
        attach_forex_opening(original,read(files),period_start=date(2025,12,1),authoritative=authoritative)
    assert original.lots(ForexPair.from_symbol('USD/MXN'))[0].quantity==40000


def test_authoritative_flat_state_cannot_be_reseeded(files):
    with pytest.raises(ValueError,match='existing opening state'):
        attach_forex_opening(LotBook(),read(files),period_start=date(2025,12,1),authoritative=True)


def test_bootstrap_does_not_invent_basis_or_canonical_campaign(files):
    e=read(files);book=attach_forex_opening(LotBook(),e,period_start=date(2025,12,1),authoritative=False)
    lot=book.lots(ForexPair.from_symbol('USD/MXN'))[0]
    assert lot.basis_total is None and lot.campaign_id is None
    assert e.source_sha256 in lot.basis_source
    assert lot.opened_at.date()==date(2025,11,30)


def test_computed_closing_must_match_independent_monthly_close(files):
    with pytest.raises(ValueError,match='Computed Forex'):
        verify_forex_ending(book_with_position(),read(files))


def test_absent_optional_statement_keeps_default_contract():
    from campaigniq.import_contract import monthly_import_contract
    assert all(r.role is not Role.SCHWAB_FOREX_MONTHLY_STATEMENT for r in monthly_import_contract(2025,12).requirements)


def preflight_inputs(tmp_path,files):
    monthly=tmp_path/'monthly.txt';monthly.write_text(TEXT)
    empty=tmp_path/'base.txt';empty.write_text('No positions\n')
    return {Role.THINKORSWIM_TRADE_HISTORY:files[0],
        Role.SCHWAB_FOREX_TRANSACTION_REPORT:files[1],
        Role.SCHWAB_FOREX_MONTHLY_STATEMENT:monthly,
        Role.SCHWAB_OPENING_POSITION_SNAPSHOT:empty,
        Role.SCHWAB_CLOSING_POSITION_SNAPSHOT:empty,
        Role.SCHWAB_REALIZED_GAIN_LOSS:empty}


def prepare_bootstrap(monkeypatch,tmp_path,inputs,forex_applicable=True):
    import campaigniq.import_preflight as module
    from campaigniq.import_validation import MonthlyInputValidation
    import campaigniq.pending_opening_inventory as pending
    monkeypatch.setattr(module,'validate_monthly_input',lambda role,*a,**kw:MonthlyInputValidation(role,True,'base fixture'))
    monkeypatch.setattr(pending,'reconstruct_pending_opening_inventory',lambda *a,**kw:LotBook())
    return module.prepare_monthly_import(2025,12,authoritative_state_root=tmp_path/'state',
        supplied_inputs=inputs,forex_applicable=forex_applicable,crypto_applicable=False)


def test_preflight_bootstrap_includes_verified_forex_opening(monkeypatch,tmp_path,files):
    result=prepare_bootstrap(monkeypatch,tmp_path,preflight_inputs(tmp_path,files))
    assert result.ready
    assert result.opening_state is None
    assert result.validation_for(Role.SCHWAB_FOREX_MONTHLY_STATEMENT).valid
    assert result.opening_lot_book.lots(ForexPair.from_symbol('USD/MXN'))[0].quantity==50000


def test_supplied_invalid_optional_evidence_blocks_ready(monkeypatch,tmp_path,files):
    inputs=preflight_inputs(tmp_path,files)
    inputs[Role.SCHWAB_FOREX_MONTHLY_STATEMENT].write_text('Unreadable')
    result=prepare_bootstrap(monkeypatch,tmp_path,inputs)
    assert not result.ready
    assert not result.validation_for(Role.SCHWAB_FOREX_MONTHLY_STATEMENT).valid
    assert not result.opening_lot_book.instruments()


def test_forex_not_applicable_cannot_accept_opening_statement(monkeypatch,tmp_path,files):
    result=prepare_bootstrap(monkeypatch,tmp_path,preflight_inputs(tmp_path,files),forex_applicable=False)
    assert not result.ready
    assert not result.validation_for(Role.SCHWAB_FOREX_MONTHLY_STATEMENT).valid


def test_missing_supporting_report_blocks_statement_evidence(monkeypatch,tmp_path,files):
    inputs=preflight_inputs(tmp_path,files);del inputs[Role.SCHWAB_FOREX_TRANSACTION_REPORT]
    result=prepare_bootstrap(monkeypatch,tmp_path,inputs)
    assert not result.ready


def test_preview_is_read_only_and_does_not_override_csv_position_control(monkeypatch,tmp_path,files):
    from campaigniq.monthly_import_execution import execute_monthly_import
    inputs=preflight_inputs(tmp_path,files)
    inputs[Role.SCHWAB_REALIZED_GAIN_LOSS].write_text('')
    inputs[Role.SCHWAB_CLOSING_POSITION_SNAPSHOT].write_text('')
    preflight=prepare_bootstrap(monkeypatch,tmp_path,inputs)
    before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result=execute_monthly_import(preflight,authoritative_state_root=tmp_path/'state',supplied_inputs=inputs,preview_only=True)
    assert not result.finalized
    assert not result.result.ending_lot_book.instruments()
    assert not result.closing_reconciliation.reconciled  # In this fixture the CSV has no later zero-position row.
    assert {p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}==before


def test_monthly_statement_draft_upload_is_restored_without_mutation():
    from campaigniq.import_draft_bridge import restore_uploads
    class Repo:
        def get(self,draft_id):return object()
        def document(self,draft_id,role):
            if role==Role.SCHWAB_FOREX_MONTHLY_STATEMENT.value:return 'monthly.PDF','application/pdf',b'original PDF bytes'
            raise FileNotFoundError
    restored=restore_uploads(Repo(),uuid4())
    assert restored[Role.SCHWAB_FOREX_MONTHLY_STATEMENT].getvalue()==b'original PDF bytes'


def test_dashboard_does_not_require_optional_statement():
    src=(Path(__file__).resolve().parents[1]/'src/campaigniq/ui/dashboard.py').read_text()
    section=src.split('    required_upload_roles = tuple(',1)[1].split('    required_uploads_supplied',1)[0]
    assert 'and role != MonthlyInputRole.SCHWAB_FOREX_MONTHLY_STATEMENT' in section
    assert 'schwab_forex_monthly_statement.txt' in src

@pytest.mark.parametrize('suffix',['.PDF','.txt'])
def test_dashboard_saves_original_monthly_upload_and_uses_extracted_text(tmp_path,suffix):
    import ast
    from campaigniq.import_draft_bridge import RestoredUpload
    source=(Path(__file__).resolve().parents[1]/'src/campaigniq/ui/dashboard.py').read_text()
    tree=ast.parse(source)
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_save_uploaded_monthly_inputs')
    namespace={'Path':Path,'MonthlyInputRole':Role,'Mapping':dict}
    def extract(src,dest):
        assert src.read_bytes()==b'original'
        dest.write_text('extracted statement')
        return dest
    namespace['extract_pdf_text']=extract
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'<upload handler>','exec'),namespace)
    supplied=namespace[fn.name](upload_dir=tmp_path,uploads={Role.SCHWAB_FOREX_MONTHLY_STATEMENT:RestoredUpload('monthly'+suffix,'application/pdf',b'original')})
    target=supplied[Role.SCHWAB_FOREX_MONTHLY_STATEMENT]
    assert (tmp_path/('schwab_forex_monthly_statement'+suffix.lower())).read_bytes()==b'original'
    assert target.read_text()==('extracted statement' if suffix=='.PDF' else 'original')
