"""Broker QUOTE_ALL exports must pass the same route as minimal CSV exports."""
import csv
import io
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from campaigniq.importers.schwab.realized_csv import HEADERS,is_csv,read
from campaigniq.importers.schwab.realized_gain_loss_reader import read_realized_gain_loss_section
from campaigniq.import_validation import validate_monthly_input
from campaigniq.import_contract import MonthlyInputRole


def export(quoting=csv.QUOTE_ALL):
    row=dict.fromkeys(HEADERS,'')
    row.update({'Symbol':'ABC','Name':'Synthetic, Inc.','Closed Date':'10/15/2026','Opened Date':'09/01/2026','Quantity':'2','Proceeds Per Share':'$100.00','Cost Per Share':'$50.00','Proceeds':'$200.00','Cost Basis (CB)':'$100.00','Gain/Loss ($)':'$100.00','Term':'Short Term','Wash Sale?':'No'})
    out=io.StringIO();w=csv.writer(out,quoting=quoting)
    w.writerow(['Realized Gain/Loss - Lot Details for Synthetic as of Mon Nov 02 07:01:42 EDT 2026 from 10/01/2026 to 10/31/2026']+['']*24)
    w.writerow(HEADERS);w.writerow([row[k] for k in HEADERS]);return out.getvalue()


class QuotedHeaderTests(unittest.TestCase):
    def test_broker_quoted_title_recognized(self):
        text=export();self.assertTrue(text.startswith('"Realized'))
        self.assertTrue(is_csv(text))
    def test_bom_quoted_header_recognized(self):self.assertTrue(is_csv('\ufeff'+export()))
    def test_quoted_and_minimal_exports_have_identical_records(self):
        self.assertEqual(read(export()),read(export(csv.QUOTE_MINIMAL)))
        self.assertEqual(read_realized_gain_loss_section(export().splitlines()),read(export()))
    def test_real_validation_path_accepts_quoted_csv(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'report.csv';path.write_text(export())
            row=validate_monthly_input(MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS,path,period_start=date(2026,10,1),period_end=date(2026,10,31))
            self.assertTrue(row.valid,row.message);self.assertEqual(row.record_count,1)
    def test_wrong_period_still_rejected(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'report.csv';path.write_text(export())
            row=validate_monthly_input(MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS,path,period_start=date(2026,9,1),period_end=date(2026,9,30))
            self.assertFalse(row.valid)
    def test_unrelated_or_malformed_headers_not_recognized(self):
        for text in ('','"unclosed','"Unrelated report",x\n','Symbol,Name\n'):
            self.assertFalse(is_csv(text))

if __name__=='__main__':unittest.main()
