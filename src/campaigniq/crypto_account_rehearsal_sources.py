"""Public synthetic crypto PDFs/export for disposable account-source rehearsals."""
from campaigniq.account_selection_rehearsal import synthetic_sources
from campaigniq.import_draft_bridge import RestoredUpload

EXPORT='Account Statement for synthetic account since 8/31/26 through 9/29/26\n\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n\nAccount Trade History\n,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type\n,9/28/26 10:22:10,CRYPTO,BUY,+0.00587022,TO OPEN,BTC/USD,,,CRYPTO,83703.30,83703.30,LMT\n,9/28/26 10:22:10,CRYPTO,BUY,+0.00005821,TO OPEN,BTC/USD,,,CRYPTO,83703.30,83703.30,LMT\n,9/28/26 10:22:10,CRYPTO,BUY,+0.00000057,TO OPEN,BTC/USD,,,CRYPTO,83703.30,83703.30,LMT\n\nForex Statements\nTrade Date,Exec Date,Exec Time,Type,Ref #,Description,Commissions & Fees,Amount,Balance\n\n"Crypto #SYNTHETIC (Crypto offered by test bank) Statements"\nTrade Date,Exec Date,Exec Time,Type,Ref #,Description,Commissions & Fees,Amount,Balance\n8/31/26,8/31/26,00:00:00,BAL,,Cash Balance,--,--,0.00\n9/27/26,9/28/26,10:20:00,FND,1,Funding,--,500.00,500.00\n9/27/26,9/28/26,10:21:00,FND,2,Funding,--,3.75,503.75\n9/27/26,9/28/26,10:22:10,TRD,100,BOT 0.00587022 BTC/USD @83703.30,-3.68,-491.36,8.71\n9/27/26,9/28/26,10:22:10,TRD,100,BOT 0.00005821 BTC/USD @83703.30,-0.04,-4.87,3.80\n9/27/26,9/28/26,10:22:10,TRD,100,BOT 0.00000057 BTC/USD @83703.30,--,-0.05,3.75\n9/29/26,9/29/26,00:00:00,BAL,,Cash Balance,--,--,3.75\n\n"Crypto #SYNTHETIC (Crypto offered by test bank)"\nSymbol,Qty,Trade Price,Total Cost,P/L Open,P/L Day,Net Liq\nBTC/USD,+0.005929,83703.8286,.00,($0.86),$0.62,$495.42\n\n'
STATEMENT='Schwab CryptoTM Account of\nAccount Number Statement Period\n****-******0841 September 1-30, 2026\nCash\nSymbol Beginning Balance($) Ending Balance($)\nCASH 3.75 3.75\nPosition Details - Crypto 1\nSymbol Name Quantity Price($) Market Value($)\nBTC/USD Bitcoin 0.005929 83,558.90 495.42\nTransaction Details\nDate Action Symbol Description Quantity Price($) Fees($) Amount($)\n09/28 BUY BTC/USD Bitcoin 0.00587022 83,703.30 (3.68) (495.04)\n      BUY BTC/USD Bitcoin 0.00005821 83,703.30 (0.04) (4.91)\n      BUY BTC/USD Bitcoin 0.00000057 83,703.30 0.00 (0.05)\n      DEPOSIT CASH Journal from brokerage account -- -- 0.00 500.00\n      DEPOSIT CASH Journal from brokerage account -- -- 0.00 3.75\nDisclosures\n'

def pdf(text):
    commands = ['BT /F1 7 Tf 9 TL 10 780 Td']
    for i, line in enumerate(text.splitlines()):
        escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        commands.append(('T* ' if i else '') + '(' + escaped + ') Tj')
    stream = '\n'.join([*commands, 'ET']).encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Count 1 /Kids [3 0 R] >>', b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 900 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>', b'<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>', b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    raw = b'%PDF-1.4\n'
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += str(i).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    xref = len(raw)
    raw += b'xref\n0 6\n0000000000 65535 f \n'
    for offset in offsets:
        raw += f'{offset:010d} 00000 n \n'.encode()
    return raw + b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n' + str(xref).encode() + b'\n%%EOF\n'


def sources(number='97531864'):
    uploads=synthetic_sources(number)
    replacements={'schwab_brokerage_statement':('January 1-31, 2025','October 1-31, 2026'),
        'schwab_opening_position_snapshot':('December 1-31, 2024','September 1-30, 2026'),
        'schwab_realized_gain_loss':('01/01/2025 to 01/31/2025','10/01/2026 to 10/31/2026')}
    for role,(old,new) in replacements.items():
        u=uploads[role];uploads[role]=RestoredUpload(u.name,u.media_type,u.content.replace(old.encode(),new.encode()))
    raw=EXPORT.replace('Account Statement for synthetic account since 8/31/26 through 9/29/26',
        'Account Statement for '+number+' since 9/30/26 through 10/31/26').replace('SYNTHETIC','TEST000099')
    raw=raw.replace('9/28/26','10/28/26').replace('9/27/26','10/27/26').replace('9/29/26','10/31/26')
    uploads['thinkorswim_trade_history']=RestoredUpload('history.csv','text/csv',raw.encode())
    previous='Schwab CryptoTM Account of\n****-******0099 September 1-30, 2026\nCASH 0.00 0.00\nPosition Details - Crypto 1\nTransaction Details\nDisclosures\n'
    current=STATEMENT.replace('September 1-30','October 1-31').replace('09/28','10/28').replace('0841','0099')
    return uploads,RestoredUpload('previous.pdf','application/pdf',pdf(previous)),RestoredUpload('current.pdf','application/pdf',pdf(current))
