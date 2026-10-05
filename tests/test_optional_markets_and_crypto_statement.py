from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from pathlib import Path
import ast
import hashlib
import pytest
from campaigniq.import_contract import MonthlyInputRole
from campaigniq.import_validation import MonthlyInputValidation
from campaigniq.domain.lot_book import LotBook
from campaigniq.persistence.artifact_storage import LocalFilesystemArtifactStorage
from campaigniq.persistence.crypto_month import build_crypto_month
from campaigniq.importers.schwab.crypto_statement_reader import read_crypto_statement, attach_crypto_statement
import campaigniq.market_applicability as markets
import campaigniq.import_preflight as preflight_module
START, END = date(2026,9,1), date(2026,9,30)
STATEMENT = '''Schwab CryptoTM Account of
Account Number Statement Period
****-******0841 September 1-30, 2026
Cash
Symbol Beginning Balance($) Ending Balance($)
CASH 3.75 3.75
Position Details - Crypto 1
Symbol Name Quantity Price($) Market Value($)
BTC/USD Bitcoin 0.005929 83,558.90 495.42
Transaction Details
Date Action Symbol Description Quantity Price($) Fees($) Amount($)
09/28 BUY BTC/USD Bitcoin 0.00587022 83,703.30 (3.68) (495.04)
      BUY BTC/USD Bitcoin 0.00005821 83,703.30 (0.04) (4.91)
      BUY BTC/USD Bitcoin 0.00000057 83,703.30 0.00 (0.05)
      DEPOSIT CASH Journal from brokerage account -- -- 0.00 500.00
      DEPOSIT CASH Journal from brokerage account -- -- 0.00 3.75
Disclosures
'''

def statement(tmp_path, text=STATEMENT):
    path=tmp_path/'crypto.txt';path.write_text(text)
    return read_crypto_statement(path,period_start=START,period_end=END)

def crypto_month(tmp_path):
    import runpy
    fixtures=runpy.run_path(str(Path(__file__).with_name('test_crypto_support.py')))
    report=fixtures['read'](tmp_path,fixtures['EXPORT'].replace('SYNTHETIC','TEST0841'))
    return build_crypto_month(report)

def test_statement_preserves_partial_fills_and_flags_cash_rollforward(tmp_path):
    parsed=statement(tmp_path)
    assert len(parsed['transactions'])==5
    assert sum(Decimal(t['quantity']) for t in parsed['transactions'] if t['action']=='BUY')==Decimal('.00592900')
    matched=attach_crypto_statement(crypto_month(tmp_path),parsed)
    assert matched['statement_fills_reconciled'] and matched['statement_ending_balances_reconciled']
    assert not matched['month_end_reconciled']
    assert Decimal(parsed['cash_rollforward_delta_usd'])==Decimal('3.75')

@pytest.mark.parametrize('old,new', [('September 1-30','August 1-30'), ('(495.04)','(494.04)'), ('0.005929 83','0.006929 83'), ('******0841','******9999')])
def test_statement_mismatches_rejected(tmp_path,old,new):
    with pytest.raises(ValueError):
        attach_crypto_statement(crypto_month(tmp_path),statement(tmp_path,STATEMENT.replace(old,new)))

def test_crypto_skip_rejects_existing_holdings(tmp_path,monkeypatch):
    monkeypatch.setattr(markets,'read_crypto_report',lambda *a,**k:None)
    monkeypatch.setattr(markets,'load_preceding_crypto',lambda *a:dict(ending_lots={'BTC/USD':[dict(quantity='0.1')]}))
    with pytest.raises(ValueError,match='Crypto cannot'):
        markets.validate_market_applicability(tmp_path/'unused',period_start=START,period_end=END,opening_lot_book=LotBook(),storage=None,crypto_applicable=False)

@pytest.mark.parametrize('report', [dict(fills=[{}]),dict(cash_ledger=[{}]),dict(last_reported_cash_usd='1')])
def test_crypto_skip_rejects_activity_and_cash(tmp_path,monkeypatch,report):
    monkeypatch.setattr(markets,'read_crypto_report',lambda *a,**k:report)
    monkeypatch.setattr(markets,'load_preceding_crypto',lambda *a:None)
    with pytest.raises(ValueError,match='Crypto cannot'):
        markets.validate_market_applicability(tmp_path/'unused',period_start=START,period_end=END,opening_lot_book=LotBook(),storage=None,crypto_applicable=False)

def test_forex_skip_rejects_export_activity(tmp_path,monkeypatch):
    reader=SimpleNamespace(read=lambda path:SimpleNamespace(sections=[SimpleNamespace(name='Forex Statements',lines=['9/28/26,9/28/26,10:00:00,TRD,1,BOT 1000 EUR/USD @1.1,0,0,0,0'])]))
    monkeypatch.setattr(markets,'ThinkorswimSourceReader',lambda:reader)
    with pytest.raises(ValueError,match='Forex activity'):
        markets.validate_market_applicability(tmp_path/'unused',period_start=START,period_end=END,opening_lot_book=LotBook(),storage=None,forex_applicable=False)

def test_preflight_skip_no_longer_requires_forex_report(tmp_path,monkeypatch):
    monkeypatch.setattr(preflight_module,'load_preceding_authoritative_state',lambda *a,**k:SimpleNamespace(lot_book=LotBook(),period_end=date(2026,8,31)))
    monkeypatch.setattr(preflight_module,'validate_monthly_input',lambda role,*a,**k:MonthlyInputValidation(role=role,valid=True,message='Valid'))
    monkeypatch.setattr(markets,'validate_market_applicability',lambda *a,**k:None)
    inputs={r:tmp_path/'file' for r in (MonthlyInputRole.THINKORSWIM_TRADE_HISTORY,MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS,MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT)}
    result=preflight_module.prepare_monthly_import(2026,9,authoritative_state_root=tmp_path,supplied_inputs=inputs,forex_applicable=False,crypto_applicable=False)
    assert result.ready and not result.forex_applicable and not result.crypto_applicable
    assert result.validation_for(MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT).valid
    assert not next(req for req in result.contract.requirements if req.role==MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT).required

def test_declaration_changes_invalidate_signature():
    tree=ast.parse(Path('src/campaigniq/ui/dashboard.py').read_text())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_monthly_import_signature')
    scope={'hashlib':hashlib,'MONTHLY_UPLOAD_ROLES':()}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'signature','exec'),scope)
    signature=scope['_monthly_import_signature']
    assert len({signature(year=2026,month=9,uploads={},forex_applicable=f,crypto_applicable=c) for f in (True,False) for c in (True,False)})==4

def test_crypto_statement_is_optional_for_existing_imports():
    from campaigniq.import_contract import monthly_import_contract
    assert MonthlyInputRole.SCHWAB_CRYPTO_STATEMENT not in {req.role for req in monthly_import_contract(2026,9).requirements if req.required}

def test_execution_without_forex_report_reaches_pipeline(tmp_path,monkeypatch):
    import campaigniq.monthly_import_execution as execution
    monkeypatch.setattr(markets,'validate_market_applicability',lambda *a,**k:None)
    monkeypatch.setattr(execution,'_verified_prior_deliveries',lambda *a,**k:())
    preflight=SimpleNamespace(ready=True,opening_lot_book=LotBook(),forex_applicable=False,crypto_applicable=False,
                             contract=SimpleNamespace(period_start=START,period_end=END))
    inputs={role:tmp_path/'file' for role in (MonthlyInputRole.THINKORSWIM_TRADE_HISTORY,MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS,MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT)}
    def pipeline(self,**kwargs):
        assert kwargs['forex_transaction_report'] is None
        raise RuntimeError('No-Forex pipeline reached')
    monkeypatch.setattr(execution.PeriodImportPipeline,'run',pipeline)
    with pytest.raises(RuntimeError,match='No-Forex pipeline reached'):
        execution.execute_monthly_import(preflight,authoritative_state_root=tmp_path,supplied_inputs=inputs)
    assert not list(tmp_path.iterdir())

def test_scope_and_choices_are_passed_to_both_signature_checks():
    tree=ast.parse(Path('src/campaigniq/ui/dashboard.py').read_text())
    wizard=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='render_monthly_import_wizard')
    signatures=[n for n in ast.walk(wizard) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='_monthly_import_signature']
    assert len(signatures)==2
    assert all({'forex_applicable','crypto_applicable'} <= {k.arg for k in call.keywords} for call in signatures)
    saving=[n for n in ast.walk(wizard) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='_save_uploaded_monthly_inputs']
    assert all({k.arg for k in call.keywords}=={'upload_dir','uploads'} for call in saving)

def test_empty_crypto_balance_row_can_be_not_applicable(tmp_path,monkeypatch):
    empty=dict(cash_ledger=[dict(type='BAL',amount_usd='0',fee_cash_change_usd='0',balance_usd='0')],last_reported_cash_usd='0')
    monkeypatch.setattr(markets,'read_crypto_report',lambda *a,**k:empty)
    monkeypatch.setattr(markets,'load_preceding_crypto',lambda *a:None)
    markets.validate_market_applicability(tmp_path/'unused',period_start=START,period_end=END,opening_lot_book=LotBook(),storage=None,crypto_applicable=False)
