from dataclasses import replace
from datetime import date,datetime
from decimal import Decimal as D
import pytest
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.value_objects.forex_pair import ForexPair
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.schwab.position_snapshot_reader import read_position_snapshot_section
from campaigniq.importers.schwab.forex_transaction_reader import SchwabForexNewTransaction, SchwabForexSettlement, SchwabForexTransactionReport
from campaigniq.closing_inventory_reconciliation import reconcile_closing_inventory
END=date(2026,9,30)
ETF=['Positions - Exchange Traded Funds','Symbol Description Quantity Price($) Market Value($) Cost Basis($) Gain/(Loss)($) Yield Income($)',
     'TEST REX AI EQUITY PREMIUM (M), 2,000.0000 37.41080 74,821.60 75,279.80 (458.20) 33.90% 25,852.30',
     'Total Exchange Traded Funds','OTHER NOT A HOLDING 100.0000 1.00000 100.00 100.00 0.00 N/A N/A']
def book(*positions):
    result=LotBook()
    for i,(instrument,qty) in enumerate(positions):
        result.seed(Lot(str(i),instrument,D(qty),datetime(2026,8,31),None))
    return result

def new(total='-50000',day=25):
    return SchwabForexNewTransaction(str(day),datetime(2026,9,day,15,47),datetime(2026,9,day,17),'USD/JPY','Sell',D('157.206'),D('-50000'),D('7860300'),D('0'),D('157.206'),D(total))
def report(*events):
    return SchwabForexTransactionReport('synthetic',date(2026,8,31),END,D('0'),D('0'),(),(),events)
def reconcile(ending,forex,opening=None,rows=()):
    return reconcile_closing_inventory(ending_lot_book=ending,snapshot_rows=rows,period_end=END,forex_report=forex,opening_lot_book=opening)

def test_etf_quantity_basis_and_section_end():
    rows=read_position_snapshot_section(ETF,snapshot_at=datetime(2026,9,30))
    assert len(rows)==1 and rows[0].quantity==D('2000')
    assert rows[0].basis_total==D('75279.80') and rows[0].instrument()==Instrument('TEST')

def test_etf_continuation():
    lines=ETF[:3]+['Positions - Exchange Traded Funds (continued)',ETF[2].replace('TEST ','NEXT '),'Total Exchange Traded Funds']
    assert len(read_position_snapshot_section(lines,snapshot_at=datetime(2026,9,30)))==2

def test_both_accounts_reconcile():
    ending=book((Instrument('TEST'),'2000'),(ForexPair.from_symbol('USD/JPY'),'-50000'))
    rows=read_position_snapshot_section(ETF,snapshot_at=datetime(2026,9,30))
    assert reconcile(ending,report(new()),rows=rows).reconciled

@pytest.mark.parametrize('qty',['-40000','50000','0'])
def test_actual_forex_mismatch_blocks(qty):
    ending=book((ForexPair.from_symbol('USD/JPY'),qty)) if qty!='0' else book()
    result=reconcile(ending,report(new()))
    assert not result.reconciled and result.mismatches[0].snapshot_quantity==D('-50000')

def test_no_activity_retains_only_authoritative_opening():
    opening=book((ForexPair.from_symbol('EUR/USD'),'10000'))
    assert reconcile(opening,report(),opening).reconciled
    assert not reconcile(book((ForexPair.from_symbol('EUR/USD'),'20000')),report(),opening).reconciled

def test_last_total_position_with_unsorted_rows():
    assert reconcile(book((ForexPair.from_symbol('USD/JPY'),'-100000')),report(new(total='-100000',day=28),new())).reconciled

def test_closed_position_checked_as_zero():
    close=SchwabForexSettlement('close',datetime(2026,9,29),datetime(2026,9,29,17),'USD/JPY','Buy',D('158'),D('50000'),D('0'),D('0'))
    r=replace(report(new()),settlements=(close,))
    assert reconcile(book(),r).reconciled
    assert not reconcile(book((ForexPair.from_symbol('USD/JPY'),'-50000')),r).reconciled

@pytest.mark.parametrize('end',[date(2026,8,31),date(2026,9,29)])
def test_report_must_cover_selected_month(end):
    with pytest.raises(ValueError,match='complete selected-month'):
        reconcile(book(),replace(report(),period_end=end))

def test_equity_discrepancy_not_suppressed():
    result=reconcile(book((Instrument('MISSING'),'100')),report())
    assert not result.reconciled and result.mismatches[0].instrument==Instrument('MISSING')

def test_forex_without_control_still_blocks():
    result=reconcile_closing_inventory(ending_lot_book=book((ForexPair.from_symbol('USD/JPY'),'-50000')),snapshot_rows=(),period_end=END)
    assert not result.reconciled
