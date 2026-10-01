"""Pending evidence reconciles settled holdings to economic closing inventory."""

import unittest
from datetime import date, datetime
from decimal import Decimal

from campaigniq.closing_inventory_reconciliation import reconcile_closing_inventory
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.schwab.pending_activity_reader import (
    SchwabPendingPositionActivity, read_pending_position_activity,
)
from campaigniq.importers.schwab.position_snapshot import SchwabPositionSnapshotRow
from campaigniq.importers.schwab.position_snapshot_reader import read_position_snapshot_section


class PendingStockSettlementTests(unittest.TestCase):
    def test_pending_stock_sale_inherits_option_activity_date(self):
        rows = read_pending_position_activity([
            "Pending / Open Activity",
            "Pending 01/30 Cover Short TEST CALL TEST CORP 1.0000 5.6500 02/02 (565.66)",
            "01/30/2026", "42.00 C",
            "Sale TEST TEST CORP 100.0000 47.6400 02/02 4,763.98",
            "Total Pending Transactions",
            "Sale OTHER OTHER CORP 100.0000 47.6400 02/02 4,763.98",
        ], period_end=date(2026, 1, 31))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].instrument, Instrument("TEST"))
        self.assertEqual(rows[1].quantity_change, Decimal("-100"))
        self.assertEqual(rows[1].activity_date, date(2026, 1, 30))
        self.assertEqual(rows[1].settlement_date, date(2026, 2, 2))

    def test_purchase_and_december_year_rollover(self):
        rows = read_pending_position_activity([
            "Pending / Open Activity",
            "Pending 12/31 Purchase TEST TEST CORP 100.0000 42.5700 01/02 (4,257.00)",
        ], period_end=date(2026, 12, 31))
        self.assertEqual(rows[0].quantity_change, Decimal("100"))
        self.assertEqual(rows[0].activity_date, date(2026, 12, 31))
        self.assertEqual(rows[0].settlement_date, date(2027, 1, 2))

    def test_option_holding_with_blank_basis_is_preserved(self):
        rows = read_position_snapshot_section([
            "Positions - Options",
            "TEST CALL TEST CORP (1.0000) S 4.22380 (422.38)",
            "$42 EXP 01/30/26", "Total Options",
        ], snapshot_at=datetime(2026, 1, 31))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].quantity, Decimal("-1"))
        self.assertIsNone(rows[0].basis_total)

    def result(self, activity, settlement, quantity="-100"):
        return reconcile_closing_inventory(
            ending_lot_book=LotBook(),
            snapshot_rows=(SchwabPositionSnapshotRow("TEST", Decimal("100"),
                                                     datetime(2026, 1, 31)),),
            pending_activity=(SchwabPendingPositionActivity(
                Instrument("TEST"), Decimal(quantity), activity, settlement),),
            period_end=date(2026, 1, 31),
        )

    def test_friday_sale_before_saturday_month_end_reconciles(self):
        self.assertTrue(self.result(date(2026, 1, 30), date(2026, 2, 2)).reconciled)

    def test_settled_activity_is_not_counted_twice(self):
        self.assertFalse(self.result(date(2026, 1, 29), date(2026, 1, 30)).reconciled)

    def test_out_of_period_or_undated_activity_does_not_adjust_holdings(self):
        self.assertFalse(self.result(date(2025, 12, 31), date(2026, 2, 2)).reconciled)
        self.assertFalse(self.result(date(2026, 2, 1), date(2026, 2, 2)).reconciled)
        self.assertFalse(self.result(None, date(2026, 2, 2)).reconciled)

    def test_earlier_activity_requires_explicit_settlement_evidence(self):
        self.assertFalse(self.result(date(2026, 1, 30), None).reconciled)

    def test_wrong_pending_quantity_remains_a_mismatch(self):
        self.assertFalse(self.result(date(2026, 1, 30), date(2026, 2, 2), "-99").reconciled)

    def test_undated_pending_stock_sale_does_not_infer_date(self):
        rows = read_pending_position_activity([
            "Pending / Open Activity",
            "Sale TEST TEST CORP 100.0000 47.6400 02/02 4,763.98",
        ], period_end=date(2026, 1, 31))
        self.assertIsNone(rows[0].activity_date)
        self.assertIsNone(rows[0].settlement_date)
