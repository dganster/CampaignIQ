"""Public synthetic ES sources for the disposable database rehearsal; no customer data."""
from decimal import Decimal as D
import csv,io
from campaigniq.account_selection_rehearsal import synthetic_sources
from campaigniq.import_draft_bridge import RestoredUpload

HEADER=['','Exec Time','Spread','Side','Qty','Pos Effect','Symbol','Exp','Strike','Type','Price','Net Price','Order Type']
CASH_HEADER=['Trade Date','Exec Date','Exec Time','Type','Ref #','Description','Misc Fees','Commissions & Fees','Amount','Balance']
FILLS=[
 ['', '10/5/26 10:00:00','VERTICAL','SELL','-2','TO OPEN','/ESZ26 1/50 30 OCT 26 (EOM)','/EWV26','110','PUT','5.00','2.00','LMT'],
 ['', '', '', 'BUY','+2','TO OPEN','/ESZ26 1/50 30 OCT 26 (EOM)','/EWV26','100','PUT','3.00','CREDIT',''],
]
LEDGER=[
 ['9/30/26','10/1/26','00:00:00','BAL','--','Opening cash','--','--','--','0.00'],
 ['10/4/26','10/5/26','10:00:00','TRD','1','SOLD -2 VERTICAL /ESZ26:XCME 1/50 30 OCT 26 (EOM) /EWV26:XCME 110/100 PUT @2.00','-2','-8','200','190'],
 ['10/4/26','10/5/26','11:00:00','FSWP','2','Cash sweep from equities account','--','--','1000','1190'],
 ['10/29/26','10/30/26','14:00:00','TRD','3','Exercise, future settle 2.0 of /ESZ26 XCME 50 (WEEKLY) 30 Oct 2026 110.0 PUT','-1','-2','--','1187'],
 ['10/29/26','10/30/26','14:00:00','TRD','4','Opening futures position due to futures option expiration','--','--','--','1187'],
 ['10/29/26','10/30/26','14:00:01','TRD','5','Exercise, future settle -2.0 of /ESZ26 XCME 50 (WEEKLY) 30 Oct 2026 100.0 PUT','-1','-2','--','1184'],
 ['10/29/26','10/30/26','14:00:01','TRD','6','Opening futures position due to futures option expiration','--','--','-1000','184'],
 ['10/31/26','10/31/26','23:00:00','BAL','--','Ending cash','--','--','--','184'],
]
PDF_HEADER='   DATE   AT   LONG/BUY   SHRT/SELL         DESCRIPTION                     PRICE/LEGND   CC       DEBIT          CREDIT'


def pdf_row(day,buy,sell,description,kind,cash):
    line=list(' '*130)
    def place(start,text): line[start:start+len(text)]=text
    place(0,day);place(9,'US');place(11,f'{buy or "":>12}');place(23,f'{sell or "":>12}')
    place(37,description)
    if kind!='SWEEP':place(69,'E');place(74,kind)
    place(90,'US')
    value=D(cash)
    if value<0:place(107-len(f'{-value:.2f}'),f'{-value:.2f}')
    if value>0:place(124-len(f'{value:.2f}'),f'{value:.2f}')
    return ''.join(line).rstrip()


def statement():
    rows=[
        pdf_row('10/05/26',None,2,'PUT OCT 26 IMM EOM M S&P 110','NET PREM','495'),
        pdf_row('10/05/26',2,None,'PUT OCT 26 IMM EOM M S&P 100','NET PREM','-305'),
        pdf_row('10/05/26',None,None,'Futures Cash Sweep','SWEEP','1000'),
        pdf_row('10/30/26',2,2,'DEC 26 EMINI S&P 500','P&L','-1000'),
        pdf_row('10/30/26',2,2,'DEC 26 EMINI S&P 500','FEE/COMM','-4'),
        pdf_row('10/30/26',None,2,'PUT OCT 26 IMM EOM M S&P 110','EXER/ASSN','-1'),
        pdf_row('10/30/26',2,None,'PUT OCT 26 IMM EOM M S&P 100','EXER/ASSN','-1'),
    ]
    return '''MONTHLY STATEMENT
STATEMENT DATE: OCT 30, 2026
''' + PDF_HEADER+'\n'+'\n'.join(rows)+'''
*SEGREGATED USD* *CONVERTED TOTAL*
BEGINNING BALANCE .00 .00
THIS MONTH'S ACTIVITY 184.00 184.00
ENDING BALANCE 184.00 184.00
NET FUTURES PROFIT OR LOSS 1004.00- 1004.00-
NET OPTION PREMIUM 188.00 188.00
TOTAL COMMISSION AND FEES 16.00- 16.00-
'''



def statement_pdf():
    lines=(statement()+'\nGMI ACCOUNT NUMBER: SYNTHETIC482\n').splitlines()
    # Place the synthetic short quantity safely inside its printed column.
    lines=[line[:23]+line[24:35]+' '+line[35:] if line[:2]=='10' else line for line in lines]
    commands=['BT /F1 6 Tf 8 TL 10 780 Td']
    for i,line in enumerate(lines):
        escaped=line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
        commands.append(('T* ' if i else '')+'('+escaped+') Tj')
    commands.append('ET');stream='\n'.join(commands).encode('ascii')
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Count 1 /Kids [3 0 R] >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 900 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    result=b'%PDF-1.4\n';offsets=[]
    for i,obj in enumerate(objects,1):
        offsets.append(len(result));result+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    xref=len(result);result+=b'xref\n0 6\n0000000000 65535 f \n'
    for offset in offsets:result+=f'{offset:010d} 00000 n \n'.encode()
    return result+b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF\n'


def sources(number='12345678'):
    result=synthetic_sources(number)
    replacements={'schwab_brokerage_statement':('January 1-31, 2025','October 1-31, 2026'),
        'schwab_opening_position_snapshot':('December 1-31, 2024','September 1-30, 2026'),
        'schwab_realized_gain_loss':('01/01/2025 to 01/31/2025','10/01/2026 to 10/31/2026')}
    for role,(old,new) in replacements.items():
        u=result[role];result[role]=RestoredUpload(u.name,u.media_type,u.content.replace(old.encode(),new.encode()))
    stream=io.StringIO();stream.write('Account Statement for '+number+' since 9/30/26 through 10/31/26\n\nAccount Trade History\n')
    csv.writer(stream).writerows([HEADER,*FILLS,['','10/15/26 10:00:00','STOCK','BUY','+10','TO OPEN','SPY','','','STOCK','100','100','LMT']])
    stream.write('\nFutures Statements\n');csv.writer(stream).writerows([CASH_HEADER,*LEDGER])
    stream.write('\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n\nForex Statements\n,Date,Time,Type,Ref #,Description,Commissions & Fees,Amount,Amount(USD),Balance\n\n')
    result['thinkorswim_trade_history']=RestoredUpload('history.csv','text/csv',stream.getvalue().encode())
    return result
