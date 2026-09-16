from datetime import date, datetime
from decimal import Decimal
from campaigniq.closing_inventory_reconciliation import reconcile_closing_inventory
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.importers.schwab.pending_activity_reader import SchwabPendingOptionActivity
from campaigniq.importers.schwab.position_snapshot import SchwabPositionSnapshotRow

def c(exp,strike):
    return OptionContract("AAPL",exp,Decimal(strike),OptionType.PUT)

def snapshot(old):
    return (SchwabPositionSnapshotRow("AAPL",Decimal("-2"),datetime(2026,8,31,23,59,59),
        old.expiration,old.strike,old.option_type),)

def book_with(new):
    book = LotBook()
    book.seed(Lot("new",new,Decimal("-2"),datetime(2026,8,31),None))
    return book

def test_exact_pending_roll_reconciles_pre_settlement_snapshot():
    old=c(date(2026,10,2),"290"); new=c(date(2026,10,16),"300")
    pending=(SchwabPendingOptionActivity(old,Decimal("2"),date(2026,8,31)),
             SchwabPendingOptionActivity(new,Decimal("-2"),date(2026,8,31)))
    r=reconcile_closing_inventory(ending_lot_book=book_with(new),snapshot_rows=snapshot(old),
        pending_activity=pending,period_end=date(2026,8,31))
    assert r.reconciled

def test_pending_quantity_must_exactly_explain_difference():
    old=c(date(2026,10,2),"290"); new=c(date(2026,10,16),"300")
    pending=(SchwabPendingOptionActivity(old,Decimal("1"),date(2026,8,31)),
             SchwabPendingOptionActivity(new,Decimal("-2"),date(2026,8,31)))
    r=reconcile_closing_inventory(ending_lot_book=book_with(new),snapshot_rows=snapshot(old),
        pending_activity=pending,period_end=date(2026,8,31))
    assert not r.reconciled

def test_non_period_end_pending_activity_is_not_applied():
    old=c(date(2026,10,2),"290"); new=c(date(2026,10,16),"300")
    pending=(SchwabPendingOptionActivity(old,Decimal("2"),date(2026,8,30)),
             SchwabPendingOptionActivity(new,Decimal("-2"),date(2026,8,30)))
    r=reconcile_closing_inventory(ending_lot_book=book_with(new),snapshot_rows=snapshot(old),
        pending_activity=pending,period_end=date(2026,8,31))
    assert not r.reconciled
