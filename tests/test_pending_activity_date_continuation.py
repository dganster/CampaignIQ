"""Regression for dated pending activity crossing Schwab statement pages."""
from datetime import date, datetime
from decimal import Decimal
import pytest
from campaigniq.importers.schwab.pending_activity_reader import read_pending_position_activity
from campaigniq.closing_inventory_reconciliation import reconcile_closing_inventory
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook


def lines():
    return [
        'Pending / Open Activity',
        'Pending 06/11 Qual Div NVDA NVIDIA CORP 500.0000 0.0100 07/03 5.00',
        '06/30 Purchase BLK BLACKROCK INC NEW 100.0000 1,049.0000 07/01 (104,900.00)',
        'Short Sale BLK CALL BLACKROCK INC NEW 1.0000 84.0000 07/01 8,399.34',
        '08/15/2025', '980.00 C',
        'Short Sale META CALL META PLATFORMS INC 1.0000 92.0000 07/01 9,199.34',
        '08/15/2025', '670.00 C',
        '\f', 'Statement Period June 1-30, 2025',
        'Pending / Open Activity (continued)',
        'Activity Type Date Action Symbol/CUSIP Description Quantity Settle/Payable Date',
        'Pending Short Sale UNH CALL UNITEDHEALTH GROUP 1.0000 54.6000 07/01 5,459.34',
        '08/15/2025', '260.00 C',
        'Purchase META META PLATFORMS INC 100.0000 746.0000 07/01 (74,600.00)',
        'Purchase UNH UNITEDHEALTH GROUP INC 100.0000 308.6997 07/01 (30,869.97)',
        'Total Pending Transactions',
    ]


def read(source):
    return read_pending_position_activity(source, period_end=date(2025,6,30))


def book(rows):
    result = LotBook()
    for i,r in enumerate(rows):
        result.seed(Lot(str(i), r.instrument, r.quantity_change, datetime(2025,6,30), None))
    return result


def reconcile(inventory, rows):
    return reconcile_closing_inventory(ending_lot_book=inventory, snapshot_rows=(),
        pending_activity=rows, period_end=date(2025,6,30))


def test_all_six_positions_inherit_trade_date_and_separate_settlement():
    rows = read(lines())
    assert len(rows) == 6
    assert all(r.activity_date == date(2025,6,30) for r in rows)
    assert all(r.settlement_date == date(2025,7,1) for r in rows)
    assert sorted(r.quantity_change for r in rows) == [Decimal('-1')]*3 + [Decimal('100')]*3


def test_exact_pending_evidence_resolves_the_five_mismatches_without_exception():
    rows = read(lines())
    inventory = book(rows)
    old_rows = tuple(r for r in rows if getattr(r.instrument,'symbol',None) == 'BLK')
    assert len(reconcile(inventory, old_rows).mismatches) == 5
    assert reconcile(inventory, rows).reconciled


@pytest.mark.parametrize('header', ['Pending / Open Activity', 'Pending / Open Activity (continued)'])
def test_undated_or_orphan_section_does_not_invent_date(header):
    rows = read([header, 'Purchase TEST TEST CORP 100.0000 10.0000 07/01 (1,000.00)'])
    assert rows[0].activity_date is None
    assert rows[0].settlement_date is None


def test_new_section_does_not_reuse_previous_date():
    source = lines() + ['Pending / Open Activity',
        'Purchase TEST TEST CORP 100.0000 10.0000 07/01 (1,000.00)']
    assert read(source)[-1].activity_date is None


def test_new_explicit_date_replaces_inherited_date():
    source = lines()
    source.insert(-1, '07/01 Purchase TEST TEST CORP 100.0000 10.0000 07/02 (1,000.00)')
    assert read(source)[-1].activity_date == date(2025,7,1)


def test_missing_pending_position_still_blocks_reconciliation():
    rows = read(lines())
    assert not reconcile(book(rows), rows[:-1]).reconciled
