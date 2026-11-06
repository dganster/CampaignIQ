"""Synthetic statement and canonical predecessor; no customer data."""
from datetime import date,datetime
from decimal import Decimal as D
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.persistence.lot_book_store import serialize_lot_book
from campaigniq.persistence.monthly_publication import PROTOCOL_FILE,_serialize_publication_protocol,_serialize_finalized_month_marker
from campaigniq.import_draft_bridge import RestoredUpload

def sources(number='24681360',*,month=10,income='59.00'):
    label='October 1-31, 2026' if month==10 else 'November 1-30, 2026'
    text=('Schwab One Account\nAccount Number    Statement Period\n'+number[:4]+'-'+number[4:]+'\n'+label+'\n'
        'Gain or (Loss) Summary\nThis 0.00 0.00 0.00 0.00 0.00 0.00\nPeriod\n'
        'Positions - Equities\nSPY SYNTHETIC ETF 100.0000 100.0000 10,000.00 9,000.00 1,000.00\nTotal Equities\n'
        'Positions - Options\nSPY CALL SYNTHETIC ETF (1.0000) S 1.00000 (100.00) (200.00) 100.00\n$110 EXP 11/20/26\nTotal Options\n'
        'Transactions - Summary\nBeginning Cash Deposits Withdrawals Purchases Sales/Redemptions Dividends/Interest Expenses Ending Cash\n'
        '$8,963.90 $0.00 $0.00 $0.00 $0.00 $'+income+' $0.00 $'+f'{D("8963.90")+D(income):,.2f}'+'\n'
        'Other Activity $0.00\nTransaction Details\nDate Category Action Symbol Description Amount\n')
    if D(income):text+='10/20 Dividend Qual. Dividend SPY SYNTHETIC ETF '+income+'\n'
    text+='Total Transactions $'+income+'\n'
    book=LotBook();at=datetime(2026,9,24,10)
    book.seed(Lot('synthetic-stock',Instrument('SPY'),D(100),at,D(9000),'synthetic-preserved','synthetic-covered-call'))
    book.seed(Lot('synthetic-short-call',OptionContract('SPY',date(2026,11,20),D(110),OptionType.CALL),D(-1),at,D(-200),'synthetic-preserved','synthetic-covered-call'))
    previous=date(2026,9,30)
    rows={PROTOCOL_FILE:_serialize_publication_protocol(first_period_end=previous),
        '2026-09-lot-book.json':serialize_lot_book(period_end=previous,lot_book=book),
        '2026-09-finalized.json':_serialize_finalized_month_marker(period_end=previous)}
    return RestoredUpload('no-trading-statement.txt','text/plain',text.encode()),rows
