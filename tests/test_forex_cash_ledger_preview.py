from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from campaigniq.forex_cash_ledger_preview import preview, money

HEADER=',Date,Time,Type,Ref #,Description,Commissions & Fees,Amount,Amount(USD),Balance'
TRADE=',10/6/26,19:04:08,TRD,="1001","BOT +50,000 USD/JPY @158.505",--,-1000,($10.00),$990.00'
FEE=',10/7/26,15:00:00,DOI,="1002",Interest charged,--,-1,($1.00),$989.00'
EXEC=',10/6/26 19:04:08,FOREX,BUY,+50000,TO CLOSE,USD/JPY,,,FOREX,158.505,158.505,STP'
EH=',Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type'

def export(execution='',filtered=False):
    return '\n'.join(['Account Statement for synthetic since 9/29/26 through 10/31/26','','Forex Statements',HEADER,
        ',9/30/26,23:00:00,BAL,--,Opening cash,--,--,--,$1000.00',TRADE,FEE,
        ',10/31/26,23:00:00,BAL,--,Closing cash,--,--,--,$989.00','',
        'Account Trade History filtered by USD/JPY' if filtered else 'Account Trade History',EH,execution,'',
        'Forex Account Summary','Forex Cash,$989.00','Forex Equity,$989.00',''])

class CashPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.args={k:Path(self.tmp.name)/(k+'.csv') for k in ('cash_ledger','trade_history','transaction_report','supplemental_history')}
        for k in ('cash_ledger','supplemental_history'):self.args[k].write_text(export(EXEC,True))
        self.args['trade_history'].write_text(export())
        self.args['transaction_report'].write_text('"Transaction Report since Sep 30, 2026 through Oct 31, 2026"\n"MTD Settled PL, USD:",-9.00\n"MTD fee, USD:",0.00\n')
        self.args['period_end']=date(2026,10,31)
    def change(self,key,old,new):
        p=self.args[key];p.write_text(p.read_text().replace(old,new))
    def test_balanced_preview_preserves_sources_and_conflicts(self):
        before={k:p.read_bytes() for k,p in self.args.items() if isinstance(p,Path)}
        r=preview(**self.args)
        self.assertEqual(r['candidate_trading_usd'],Decimal('-10'))
        self.assertEqual(r['candidate_financing_usd'],Decimal('-1'))
        self.assertEqual(r['candidate_net_cash_change_usd'],Decimal('-11'))
        self.assertEqual(r['csv_mtd_minus_candidate_usd'],Decimal('1'))
        self.assertEqual(r['omitted_execution_references'],['1001'])
        self.assertEqual(r['supplemental_execution_count'],1)
        for key in ('publication_authorized','account_identity_verified','closing_inventory_verified'):self.assertIs(r[key],False)
        self.assertEqual(len(r['source_sha256']),4)
        self.assertEqual(before,{k:self.args[k].read_bytes() for k in before})
    def test_invalid_cash_rows_rejected(self):
        original=self.args['cash_ledger'].read_text()
        for old,new in [('($10.00)','($11.00)'),('$990.00','$991.00'),(TRADE,TRADE+'\n'+TRADE),
            ('10/31/26','10/30/26'),('9/30/26','10/1/26'),(',DOI,',',TRANSFER,'),(',--,-1000',',1.00,-1000'),('$990.00','NaN')]:
            with self.subTest(change=(old,new)):
                self.args['cash_ledger'].write_text(original.replace(old,new))
                with self.assertRaises(ValueError):preview(**self.args)
    def test_missing_supplement_does_not_invent_execution(self):
        self.args.pop('supplemental_history')
        with self.assertRaisesRegex(ValueError,'executions'):preview(**self.args)
    def test_overlapping_supplement_rejected(self):
        self.args['trade_history'].write_text(export(EXEC))
        with self.assertRaisesRegex(ValueError,'overlapping'):preview(**self.args)
    def test_changed_execution_price_rejected(self):
        self.change('supplemental_history','158.505,158.505','158.506,158.506')
        with self.assertRaisesRegex(ValueError,'executions'):preview(**self.args)
    def test_changed_period_rejected(self):
        self.change('transaction_report','Sep 30','Sep 29')
        with self.assertRaisesRegex(ValueError,'October'):preview(**self.args)
    def test_unknown_transaction_reference_rejected(self):
        p=self.args['transaction_report'];p.write_text(p.read_text()+'1009,"Oct 6, 2026 21:04:08","Oct 7, 2026 17:00:00",settlement,USD/JPY,Buy,158.505,50000,-7925250,0,,"-10 USD",0\n')
        with self.assertRaisesRegex(ValueError,'reference'):preview(**self.args)
    def test_other_month_rejected(self):
        self.args['period_end']=date(2026,11,30)
        with self.assertRaises(ValueError):preview(**self.args)
    def test_nonfinite_money_rejected(self):
        for v in ('NaN','Infinity','-Infinity'):
            with self.subTest(v=v),self.assertRaises(ValueError):money(v)

if __name__=='__main__':unittest.main()
