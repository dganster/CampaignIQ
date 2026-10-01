"""Regression coverage for column-oriented browser print layouts."""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from campaigniq.import_contract import MonthlyInputRole
from campaigniq.import_validation import _realized_report_period, validate_monthly_input
from campaigniq.importers.schwab.realized_gain_loss_reader import read_realized_gain_loss_section


class SchwabPrintLayoutTests(unittest.TestCase):
    def validate(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.txt"
            path.write_text(text)
            return validate_monthly_input(
                MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS, path,
                period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
            )

    def test_column_labels_then_dates_use_declared_reporting_period(self):
        text = ("Realized Gain / Loss Updated: 09/23/2026\n"
                "From mm/dd/yyyy To mm/dd/yyyy\n"
                "01/01/2026 01/31/2026\n"
                "Reporting Period             Gain/Loss\n\n"
                "01/01/2026 to 01/31/2026       Long Term $0.00\n"
                "TEST 01/08/2026 100 $12.00 FIFO $1,200.00 $1,000.00 +$200.00\n"
                "TEST 01/30/2026 10.00 C 02/02/2026 1 $2.00 FIFO $200.00 $300.00 -$100.00\n")
        result = self.validate(text)
        self.assertTrue(result.valid, result.message)
        self.assertEqual(result.record_count, 2)

    def test_wrong_declared_month_remains_invalid(self):
        result = self.validate("Realized Gain / Loss\nReporting Period\n02/01/2026 to 02/28/2026\n")
        self.assertFalse(result.valid)
        self.assertIn("2026-02-01", result.message)

    def test_transaction_dates_cannot_replace_a_missing_report_period(self):
        result = self.validate("Realized Gain / Loss\nTEST 01/08/2026 100 $12.00 FIFO $1,200.00 $1,000.00 +$200.00\n")
        self.assertFalse(result.valid)

    def test_legacy_date_layout_still_works(self):
        self.assertEqual(_realized_report_period(
            "From mm/dd/yyyy 01/01/2026\nTo mm/dd/yyyy 01/31/2026"),
            (date(2026, 1, 1), date(2026, 1, 31)))

    def test_conflicting_declared_periods_fail_closed(self):
        self.assertIsNone(_realized_report_period(
            "From mm/dd/yyyy 01/01/2026 To mm/dd/yyyy 01/31/2026\n"
            "Reporting Period\n02/01/2026 to 02/28/2026"))

    def test_total_only_rows_reconcile_and_preserve_settlement_spillover(self):
        rows = read_realized_gain_loss_section([
            "TEST 01/08/2026 100 $12.00 FIFO $1,200.00 $1,000.00 +$200.00",
            "TEST 01/30/2026 10.00 C 02/02/2026 1 $2.00 FIFO $200.00 $300.00 -$100.00",
        ])
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row.gain_loss for row in rows), Decimal("100"))
        self.assertTrue(all(row.expected_gain_loss == row.gain_loss for row in rows))
        self.assertTrue(all(row.term == "UNKNOWN" for row in rows))
        self.assertEqual(rows[1].closed_date, date(2026, 2, 2))

    def test_legacy_term_and_disallowed_loss_are_preserved(self):
        rows = read_realized_gain_loss_section([
            "TEST 01/08/2026 100 $12.00 FIFO $1,200.00 $1,000.00 +$200.00 +$200.00",
            "TEST 01/30/2026 10.00 C 01/08/2026 1 $2.00 FIFO $200.00 $300.00 -$50.00",
            "Disallowed Loss: $50.00",
        ])
        self.assertEqual(rows[0].term, "SHORT")
        self.assertEqual(rows[1].disallowed_loss, Decimal("50"))
        self.assertEqual(rows[1].expected_gain_loss, rows[1].gain_loss)
