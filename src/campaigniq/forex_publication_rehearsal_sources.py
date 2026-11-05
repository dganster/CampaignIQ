"""Synthetic ordinary and combined sources for approved Forex publication."""
import csv
import io
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.forex_cash_rehearsal_sources import sources as cash_sources
from campaigniq.forex_daily_rehearsal_sources import upload as daily_upload
from campaigniq.account_selection_rehearsal import synthetic_sources
from campaigniq.crypto_account_rehearsal_sources import sources as crypto_sources


def sources(securities='24681358',forex='8642098',*,combined=False):
    originals,cash,supplement=cash_sources()
    def renumber(upload):
        return RestoredUpload(upload.name,upload.media_type,upload.content.replace(b'24681357',securities.encode()).replace(b'8642097',forex.encode()))
    original={role:renumber(u) for role,u in originals.items()};cash=renumber(cash);supplement=renumber(supplement)
    previous=current=future=None
    if combined:
        crypto,previous,current=crypto_sources(securities)
        history=crypto['thinkorswim_trade_history'].content.decode()
        full_ledger=original['thinkorswim_trade_history'].content.decode().split('Forex Statements\n',1)[1].split('\n\n',1)[0]
        lines=history.splitlines(keepends=True);start=next(i for i,l in enumerate(lines) if l.strip()=='Forex Statements')+1
        end=start
        while end<len(lines) and lines[end].strip():end+=1
        history=''.join(lines[:start])+full_ledger+'\n'+''.join(lines[end:])
        from campaigniq.futures_rehearsal_sources import FILLS,LEDGER,CASH_HEADER,statement_pdf
        def rows(values):
            out=io.StringIO();csv.writer(out,lineterminator='\n').writerows(values);return out.getvalue()
        history=history.replace(',10/15/26 10:00:00,STOCK',rows(FILLS)+',10/15/26 10:00:00,STOCK')
        history=history.replace('Forex Statements\n','Futures Statements\n'+rows([CASH_HEADER,*LEDGER])+'\nForex Statements\n')
        future=RestoredUpload('futures.pdf','application/pdf',statement_pdf())
    else:
        history=original['thinkorswim_trade_history'].content.decode()
        header=',Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type\n'
        history=history.replace(header,header+',10/15/26 10:00:00,STOCK,BUY,+10,TO OPEN,SPY,,,STOCK,100,100,LMT\n')
        history+='\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n\n'
    original['thinkorswim_trade_history']=RestoredUpload('history.csv','text/csv',history.encode())
    base=synthetic_sources(securities)
    for role,old,new in (
        ('schwab_brokerage_statement','January 1-31, 2025','October 1-31, 2026'),
        ('schwab_realized_gain_loss','01/01/2025 to 01/31/2025','10/01/2026 to 10/31/2026')):
        source=base[role];text=source.content.decode().replace(old,new)
        if role=='schwab_brokerage_statement':text=text.replace('110.0000','10.0000').replace('11,000.00','1,000.00')
        original[role]=RestoredUpload(source.name,source.media_type,text.encode())
    return original,cash,supplement,daily_upload(forex),previous,current,future
