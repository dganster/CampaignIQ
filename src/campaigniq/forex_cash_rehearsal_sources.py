"""Public synthetic CSV bytes for the fixed local Forex source-save rehearsal."""
from campaigniq.import_draft_bridge import RestoredUpload


def sources():
    header = ',Date,Time,Type,Ref #,Description,Commissions & Fees,Amount,Amount(USD),Balance'
    execution_header = ',Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type'
    execution = ',10/6/26 19:04:08,FOREX,BUY,+50000,TO CLOSE,USD/JPY,,,FOREX,158.505,158.505,STP'

    def history(*, supplemental=False):
        return '\n'.join([
            'Account Statement for 24681357 since 9/29/26 through 10/31/26', '',
            'Forex Statements', header,
            ',9/30/26,23:00:00,BAL,--,Opening cash,--,--,--,$1000.00',
            ',10/6/26,19:04:08,TRD,="1001","BOT +50,000 USD/JPY @158.505",--,-1000,($10.00),$990.00',
            ',10/7/26,15:00:00,DOI,="1002",Interest charged,--,-1,($1.00),$989.00',
            ',10/31/26,23:00:00,BAL,--,Closing cash,--,--,--,$989.00', '',
            'Account Trade History filtered by USD/JPY' if supplemental else 'Account Trade History',
            execution_header, execution if supplemental else '', '',
            'Forex Account Summary', 'Forex Cash,$989.00', 'Forex Equity,$989.00', '',
        ]).encode()

    report = (
        '"Transaction Report since Sep 30, 2026 through Oct 31, 2026"\n'
        '"MTD Settled PL, USD:",-9.00\n"MTD fee, USD:",0.00\n'
        '"Account (USD): 8642097, Base currency: USD"\n'
    ).encode()
    return (
        {'thinkorswim_trade_history': RestoredUpload('history.csv', 'text/csv', history()),
         'schwab_forex_transaction_report': RestoredUpload('forex.csv', 'text/csv', report)},
        RestoredUpload('cash.csv', 'text/csv', history(supplemental=True)),
        RestoredUpload('supplement.csv', 'text/csv', history(supplemental=True)),
    )
