"""Pending purchases establish holdings; unexplained reductions stay blocked."""
from datetime import date, datetime
from decimal import Decimal
import pytest
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.schwab.position_snapshot import SchwabPositionSnapshotRow
from campaigniq.pending_opening_inventory import reconstruct_pending_opening_inventory


def reconstruct(activity, snapshot=()):
    return reconstruct_pending_opening_inventory(snapshot,
        ['Pending / Open Activity', *activity, 'Total Pending Transactions'],
        period_end=date(2025,6,30))


def stock(action='Purchase', qty='100.0000', day='06/30', settle='07/01'):
    return f'{day} {action} BLK BLACKROCK INC NEW {qty} 1049.0000 {settle} (104,900.00)'


def snapshot(qty='100', basis='10000'):
    return (SchwabPositionSnapshotRow('BLK', Decimal(qty), datetime(2025,6,30),
        basis_total=Decimal(basis)),)


def quantity(book):
    return sum((lot.quantity for lot in book.lots(Instrument('BLK'))), Decimal('0'))


def test_purchase_opens_long_without_inventing_basis():
    book = reconstruct([stock()])
    assert quantity(book) == Decimal('100')
    lot, = book.lots(Instrument('BLK'))
    assert lot.basis_total is None and lot.basis_source is None
    assert lot.opened_at.date() == date(2025,6,30)


def test_purchase_adds_separate_lot_preserving_existing_basis():
    book = reconstruct([stock()], snapshot())
    assert quantity(book) == Decimal('200')
    assert [lot.basis_total for lot in book.lots(Instrument('BLK'))] == [Decimal('10000'), None]


def test_sale_reduces_existing_long_and_basis_proportionately():
    book = reconstruct([stock('Sale','40.0000')], snapshot())
    assert quantity(book) == Decimal('60')
    assert book.lots(Instrument('BLK'))[0].basis_total == Decimal('6000')


@pytest.mark.parametrize('settled', [(), snapshot('-100','-10000')])
def test_unmatched_sale_cannot_create_or_increase_short(settled):
    with pytest.raises(ValueError, match='no matching long inventory'):
        reconstruct([stock('Sale')], settled)


def test_oversale_stays_blocked():
    with pytest.raises(ValueError, match='exceeds settled inventory'):
        reconstruct([stock('Sale','101.0000')], snapshot())


def test_unmatched_option_cover_stays_blocked():
    with pytest.raises(ValueError, match='no matching short inventory'):
        reconstruct(['06/30 Cover Short BLK CALL BLACKROCK INC NEW 1.0000 84.0000 07/01',
                     '08/15/2025', '980.00 C'])


def test_settled_purchase_is_not_applied_twice():
    assert quantity(reconstruct([stock(settle='06/30')], snapshot())) == Decimal('100')


def test_future_purchase_does_not_enter_june_close():
    assert quantity(reconstruct([stock(day='07/01',settle='07/02')])) == 0


def test_unknown_activity_date_stays_blocked():
    with pytest.raises(ValueError, match='unknown date'):
        reconstruct(['Purchase BLK BLACKROCK INC NEW 100.0000 1049.0000 07/01 (104,900.00)'])


def test_purchase_and_sale_offset_without_phantom_lots():
    assert quantity(reconstruct([stock(),stock('Sale')])) == 0
