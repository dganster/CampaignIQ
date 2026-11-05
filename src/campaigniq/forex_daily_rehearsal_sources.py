"""Synthetic daily Forex originals matching the fixed public source-save rehearsal."""
from datetime import date, timedelta
import io
import zipfile
from campaigniq.crypto_account_rehearsal_sources import pdf
from campaigniq.import_draft_bridge import RestoredUpload


def reports(number='8642097', *, complete=False):
    previous = date(2026,9,30)
    current = date(2026,10,1)
    result = []
    last = date(2026,10,30) if complete else date(2026,10,7)
    cash = '1000.00'
    while current <= last:
        if current.weekday() >= 5:
            current += timedelta(days=1);continue
        close = current == date(2026,10,7)
        open_position = current < date(2026,10,7)
        after = '989.00' if close else cash
        def printed(d):return d.strftime('%A, %B ')+str(d.day)+d.strftime(', %Y')
        text = ('Customer Account Summary Report since '+printed(previous)+' 16:00:00 (CST) through '+printed(current)+' 16:00:00 (CST)\n'
            'Equity Summary\nBeginning Cash Balance $'+cash+'\nBeginning Account Equity $'+cash+'\n'
            'Deposits $0.00\nWithdrawals $0.00\nAdjustments $0.00\nFinancing '+('($1.00)' if close else '$0.00')+'\n'
            'Settled Profit/Loss '+('($10.00)' if close else '$0.00')+'\nUnrealized Profit/Loss $0.00\nFloating Profit/Loss $0.00\n'
            'Ending Cash Balance $'+after+'\nEnding Account Equity $'+after+'\n'
            'Account Account Currency\n'+number+' USD\nStatement From Statement To\n'
            'Profit/Loss Summary\nCurrency Pair Settled Profit/Loss Unrealized Profit/Loss Floating Profit/Loss\n'
            'USD/JPY '+('($10.00)' if close else '$0.00')+' $0.00 $0.00\n'
            'Open Position(s)\nCurrency Pair Buy/Sell Quantity Trade Price Floating Profit/Loss USD Margin Requirement USD\n')
        if open_position:
            text += ('USD/JPY\nTR Details: 9/25/2026 3:47:16 PM Sell (50,000) 157.2060000\n'
                     'USD/JPY - TOTAL POSITION: Sell (50,000) $0.00 $2500.00\n')
        text += 'Floating Profit/Loss Net USD :\nClosed Position(s)\nTrade Activity Detail\n'
        if close:text += '2026-10-06 21:04:08 USD/JPY STOP B CLOSE 50,000 158.505 1001 $0.00\n'
        text += 'Financing Report\n'
        if close:text += '2026-10-06 17:00:00 USD/JPY (50,000) 158.505 1002 $0.01 ($1.00)\n'
        text += 'Conversion Report\n'
        result.append(('daily-'+str(current)+'.pdf', text))
        cash, previous = after, current
        current += timedelta(days=1)
    return result


def upload(number='8642097', *, complete=False):
    output = io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,text in reports(number, complete=complete):
            info=zipfile.ZipInfo(name,date_time=(2026,10,31,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,pdf(text))
    return RestoredUpload('synthetic-daily-reports.zip','application/zip',output.getvalue())
