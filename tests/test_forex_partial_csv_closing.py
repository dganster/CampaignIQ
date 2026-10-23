"""A carried opening balance is not an explicit CSV transaction position."""
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import pytest
from tests.test_forex_opening_statement import files
from tests.test_forex_statement_closing_control import controls,reconcile,settlement
from campaigniq.forex_opening_statement import attach_forex_opening
from campaigniq.domain.lot_book import LotBook


def mixed(files,explicit_quantity=Decimal(0)):
    e,_,r=controls(files)
    # The monthly/history evidence includes a close of each opening pair;
    # the CSV omits the USD/MXN close but explicitly closes EUR/USD.
    e=replace(e,opening=(('USD/MXN',Decimal(50000)),('EUR/USD',Decimal(-50000))),
        execution_ids=('1004840489728','eur-close'))
    opening=attach_forex_opening(LotBook(),e,period_start=e.period_end.replace(day=1),authoritative=False)
    row=replace(settlement(reference='eur-close'),instrument='EUR/USD',side='B',amount=Decimal(50000),total_position=explicit_quantity)
    return e,opening,replace(r,settlements=(row,))


def test_partial_csv_uses_monthly_close_and_preserves_missing_reference(files):
    e,b,r=mixed(files)
    result=reconcile(files,forex_closing_evidence=e,opening_lot_book=b,forex_report=r)
    assert result.reconciled
    assert len(result.forex_source_findings)==2
    assert '1004840489728' in result.forex_source_findings[0]
    assert 'eur-close' not in result.forex_source_findings[0]
    assert r.settlements[0].total_position==0
    assert sum(l.quantity for l in b.lots(next(i for i in b.instruments() if i.symbol=='USD/MXN')))==50000


@pytest.mark.parametrize('quantity',[Decimal(1),Decimal(-50000)])
def test_explicit_contradiction_still_blocks_with_another_missing_pair(files,quantity):
    e,b,r=mixed(files,quantity)
    with pytest.raises(ValueError,match='Explicit CSV'):
        reconcile(files,forex_closing_evidence=e,opening_lot_book=b,forex_report=r)


def test_no_monthly_evidence_keeps_inferred_csv_balance_mismatch(files):
    e,b,r=mixed(files)
    result=reconcile(files,forex_closing_evidence=None,opening_lot_book=b,forex_report=r)
    assert not result.reconciled
    assert result.mismatches[0].instrument.symbol=='USD/MXN'
    assert result.mismatches[0].snapshot_quantity==50000


def test_unexplained_carry_disagreement_without_missing_execution_blocks(files):
    e,b,r=mixed(files)
    e=replace(e,execution_ids=('eur-close',))
    with pytest.raises(ValueError,match='Unexplained'):
        reconcile(files,forex_closing_evidence=e,opening_lot_book=b,forex_report=r)


def test_latest_explicit_position_is_checked_independently_of_row_input_order(files):
    e,b,r=mixed(files)
    earlier=replace(r.settlements[0],order_id='eur-earlier',trade_at=datetime(2025,12,4),settlement_at=datetime(2025,12,4),total_position=Decimal(-50000))
    e=replace(e,execution_ids=(*e.execution_ids,'eur-earlier'))
    for rows in ((earlier,r.settlements[0]),(r.settlements[0],earlier)):
        result=reconcile(files,forex_closing_evidence=e,opening_lot_book=b,forex_report=replace(r,settlements=rows))
        assert result.reconciled and result.forex_source_findings
