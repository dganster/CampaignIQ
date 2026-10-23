"""Independent monthly quantity controls preserve CSV findings and block writes."""
from dataclasses import replace
from datetime import date,datetime
from decimal import Decimal
import pytest
from tests.test_forex_opening_statement import files,read,preflight_inputs,prepare_bootstrap
from campaigniq.forex_opening_statement import attach_forex_opening
from campaigniq.closing_inventory_reconciliation import reconcile_closing_inventory
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.lot import Lot
from campaigniq.domain.value_objects.forex_pair import ForexPair
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.schwab.forex_transaction_reader import read_forex_transaction_report,SchwabForexSettlement
from campaigniq.monthly_import_execution import execute_monthly_import

END=date(2025,12,31)
PAIR=ForexPair.from_symbol('USD/MXN')


def controls(files):
    e=read(files)
    return e,attach_forex_opening(LotBook(),e,period_start=date(2025,12,1),authoritative=False),read_forex_transaction_report(files[1])


def reconcile(files,**kw):
    e,b,r=controls(files)
    params=dict(ending_lot_book=LotBook(),snapshot_rows=(),period_end=END,forex_report=r,opening_lot_book=b,forex_closing_evidence=e)
    params.update(kw)
    return reconcile_closing_inventory(**params)


def test_independent_flat_close_reconciles_but_retains_csv_omission(files):
    result=reconcile(files)
    assert result.reconciled
    assert len(result.forex_source_findings)==2
    assert '1004840489728' in result.forex_source_findings[0]
    assert 'Publication is blocked' in result.forex_source_findings[0]


def test_no_monthly_evidence_retains_original_csv_control(files):
    result=reconcile(files,forex_closing_evidence=None)
    assert not result.reconciled and not result.forex_source_findings
    assert result.mismatches[0].snapshot_quantity==50000


def test_computed_position_cannot_replace_independent_control(files):
    e,b,r=controls(files)
    result=reconcile(files,ending_lot_book=b)
    assert not result.reconciled
    assert result.mismatches[0].computed_quantity==50000
    assert result.mismatches[0].snapshot_quantity==0


def test_other_assets_still_reconcile_independently(files):
    b=LotBook();b.seed(Lot('equity',Instrument('TEST'),Decimal(1),datetime(2025,12,1),None))
    result=reconcile(files,ending_lot_book=b)
    assert not result.reconciled and result.mismatches[0].instrument==Instrument('TEST')


@pytest.mark.parametrize('change',[{'period_end':date(2025,11,30)},{'opening_lot_book':None},{'forex_report':None},{'forex_closing_evidence':{} }])
def test_incomplete_or_unbound_evidence_rejected(files,change):
    with pytest.raises(ValueError):reconcile(files,**change)


def test_different_actual_opening_cannot_be_overwritten(files):
    with pytest.raises(ValueError,match='opening inventory'):reconcile(files,opening_lot_book=LotBook())


@pytest.mark.parametrize('quantity',[Decimal('NaN'),Decimal('Infinity'),Decimal(0)])
def test_invalid_monthly_quantities_block(files,quantity):
    e,_,_=controls(files)
    with pytest.raises(ValueError):reconcile(files,forex_closing_evidence=replace(e,closing=(('USD/MXN',quantity),)))


def test_duplicate_monthly_pairs_block(files):
    e,_,_=controls(files)
    with pytest.raises(ValueError):reconcile(files,forex_closing_evidence=replace(e,closing=(('USD/MXN',Decimal(1)),)*2))


def settlement(reference='1004840489728',position=Decimal(0)):
    return SchwabForexSettlement(reference,datetime(2025,12,5),datetime(2025,12,5),'USD/MXN','S',Decimal('18.15063'),Decimal(-50000),Decimal('-1008.60'),position)


def test_explicit_conflicting_csv_total_position_blocks(files):
    e,b,r=controls(files)
    with pytest.raises(ValueError,match='Total Position'):
        reconcile(files,forex_report=replace(r,settlements=(settlement(position=Decimal(50000)),)))


def test_unknown_csv_execution_cannot_be_discarded(files):
    e,b,r=controls(files)
    with pytest.raises(ValueError,match='references conflict'):
        reconcile(files,forex_report=replace(r,settlements=(settlement(reference='unexpected'),)))


def test_matching_sources_do_not_add_findings(files):
    e,b,r=controls(files)
    result=reconcile(files,forex_report=replace(r,settlements=(settlement(),)))
    assert result.reconciled and not result.forex_source_findings


def test_unexplained_disagreement_without_missing_reference_blocks(files):
    e,_,_=controls(files)
    with pytest.raises(ValueError,match='Unexplained'):
        reconcile(files,forex_closing_evidence=replace(e,execution_ids=()))


def test_csv_monetary_controls_remain_failed_and_unmodified(files):
    e,b,r=controls(files)
    r=replace(r,mtd_settled_pl_usd=Decimal('-1008.60'))
    result=reconcile(files,forex_report=r)
    assert result.reconciled and result.forex_source_findings
    assert r.settlements==() and not r.settlement_control_reconciled
    assert r.settlement_control_delta_usd==Decimal('-1008.60')


def test_preview_is_read_only_and_finalization_refuses_unresolved_sources(monkeypatch,tmp_path,files):
    inputs=preflight_inputs(tmp_path,files)
    from campaigniq.import_contract import MonthlyInputRole as Role
    inputs[Role.SCHWAB_REALIZED_GAIN_LOSS].write_text('')
    inputs[Role.SCHWAB_CLOSING_POSITION_SNAPSHOT].write_text('')
    preflight=prepare_bootstrap(monkeypatch,tmp_path,inputs)
    before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    preview=execute_monthly_import(preflight,authoritative_state_root=tmp_path/'state',supplied_inputs=inputs,preview_only=True)
    assert preview.closing_reconciliation.reconciled and not preview.finalized
    assert preview.closing_reconciliation.forex_source_findings
    for decision in (None,object()):
        with pytest.raises(ValueError,match='does not authorize publication'):
            execute_monthly_import(preflight,authoritative_state_root=tmp_path/'state',supplied_inputs=inputs,reconciliation_decision=decision)
    assert before=={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
