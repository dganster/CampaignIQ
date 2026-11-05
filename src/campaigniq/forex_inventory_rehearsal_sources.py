"""Public synthetic October Forex monthly statement and predecessor lots."""
from datetime import date, datetime
from decimal import Decimal
from campaigniq.crypto_account_rehearsal_sources import pdf
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.value_objects.forex_pair import ForexPair
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.lot_book_store import serialize_lot_book


def statement(number='8642097'):
    account = 'Account Account Currency\n'+number+' USD\nStatement From Statement To\n'
    heading = 'Currency Pair Buy/Sell Quantity Trade Price Floating Profit/Loss USD Margin Requirement USD\n'
    footer = 'Floating Profit/Loss Net USD :\n'
    summary = ('Customer Account Summary Report since Wednesday, September 30, 2026 16:00:00 (CST) through Friday, October 30, 2026 16:00:00 (CST)\n'
        'Monthly Equity Summary\n'+account+'Open Position(s) at End of Month\n'+heading+footer)
    first = ('Customer Account Summary Report since Wednesday, September 30, 2026 16:00:00 (CST) through Thursday, October 1, 2026 16:00:00 (CST)\n'
        +account+'Open Position(s)\n'+heading+'USD/JPY\n'
        'TR Details: 9/25/2026 3:47:16 PM Sell (50,000) 157.2060000\n'
        'USD/JPY - TOTAL POSITION: Sell (50,000) ($69.49) $2,500.00\n'+footer+'Closed Position(s)\nTrade Activity Detail\nFinancing Report\n')
    execution = ('Customer Account Summary Report since Monday, October 5, 2026 16:00:00 (CST) through Tuesday, October 6, 2026 16:00:00 (CST)\n'
        +account+'Trade Activity Detail\n2026-10-06 21:04:08 USD/JPY STOP B CLOSE 50,000 158.505 1001 $0.00\nFinancing Report\n')
    return '\n'.join((summary, first, execution))


def monthly_upload(number='8642097'):
    return RestoredUpload('synthetic-october-forex.pdf', 'application/pdf', pdf(statement(number)))


def predecessor():
    book = LotBook()
    book.seed(Lot('synthetic-forex-opening', ForexPair.from_symbol('USD/JPY'), Decimal('-50000'),
                  datetime(2026, 9, 25, 15, 47, 16), None, campaign_id='synthetic-existing-forex-campaign'))
    return serialize_lot_book(period_end=date(2026, 9, 30), lot_book=book)
