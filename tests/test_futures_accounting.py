"""Synthetic ES exercise/assignment sources, separate from all customer data."""
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal as D
from pathlib import Path
import csv
import io
import json
import subprocess
import sys

import pytest

from campaigniq.futures_accounting import (
    ESFuture, ESOption, OptionFill, Delivery, LotBook, read_fills, read_ledger, replay, preview,
)
from campaigniq.futures_statement_activity import read_activity, verify_activity
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader

FUTURE=ESFuture('/ESZ26')
PUT=ESOption(FUTURE,'/EWV26',date(2026,10,30),D(110),'PUT')
LOW=replace(PUT,strike=D(100))
STAMP=datetime(2026,10,5,10)
END=datetime(2026,10,30,14)
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


def write_source(tmp_path, fills=None, ledger=None):
    stream=io.StringIO(); stream.write('Account Statement for synthetic\n\nAccount Trade History\n')
    csv.writer(stream).writerows([HEADER,*(FILLS if fills is None else fills)])
    stream.write('\nFutures Statements\n')
    csv.writer(stream).writerows([CASH_HEADER,*(LEDGER if ledger is None else ledger)])
    stream.write('\n');path=tmp_path/'history.csv';path.write_text(stream.getvalue());return path


def test_complete_synthetic_exercise_preview_has_correct_cash_and_remains_unapproved(tmp_path):
    path=write_source(tmp_path);before=path.read_bytes()
    result=preview(path,statement(),year=2026,month=10)
    assert D(result['option_realized_usd'])==200
    assert D(result['futures_realized_usd'])==-1000
    assert D(result['realized_net_usd'])==-816
    assert D(result['fees_cash_usd'])==-16
    assert result['calculated_ending_options']==result['calculated_ending_futures']=={}
    assert all(result['statement_activity_checks'].values())
    assert result['opening_positions_verified'] is False
    assert result['ending_positions_independently_verified'] is False
    assert result['publication_authorized'] is result['production_integration_enabled'] is False
    assert path.read_bytes()==before


@pytest.mark.parametrize('symbol',['/MESZ26','/NQZ26','/SMEZ26','/ESV26','ESZ26'])
def test_other_products_cannot_inherit_es_multiplier(symbol):
    with pytest.raises(ValueError):ESFuture(symbol)


@pytest.mark.parametrize('series',['/SMEV26','/ESV26','/EWZ26','/EWV25','/E6AV26','/EW5V26'])
def test_unsupported_or_conflicting_series_fail(series):
    with pytest.raises(ValueError):replace(PUT,series=series)


@pytest.mark.parametrize('q,p',[('0','1'),('1.1','1'),('NaN','1'),('1','Infinity'),('1','-.01'),('1','.0001')])
def test_invalid_fills_fail(q,p):
    with pytest.raises(ValueError):OptionFill('r','o',STAMP,PUT,D(q),'TO OPEN',D(p))


def test_fifo_partial_closes_preserve_basis():
    book=LotBook();book.open(PUT,D(-2),D(5),'a');book.open(PUT,D(-3),D(7),'b')
    assert book.close(PUT,D(4),D(2))==800
    assert book.positions()=={PUT:D(-1)}
    assert book.close(PUT,D(1),D(0))==350
    assert book.positions()=={} and book.realized==1150


@pytest.mark.parametrize('quantity',['-1','0','3'])
def test_close_cannot_flip_or_invent_opening_lots(quantity):
    b=LotBook();b.open(PUT,D(-2),D(5),'a')
    with pytest.raises(ValueError):b.close(PUT,D(quantity),D(0))


def test_call_exercise_delivers_in_same_option_direction_and_put_in_opposite():
    call=replace(PUT,kind='CALL')
    fills=[OptionFill('r','o',STAMP,call,D(2),'TO OPEN',D(5))]
    delivery=Delivery('e','c',END,FUTURE,call.expiration,call.strike,'CALL',D(2),D(0),D(-1))
    options,futures,events=replay(fills,[delivery])
    assert options.positions()=={} and futures.positions()=={FUTURE:D(2)}
    assert events[0]['option_quantity_removed']=='2'


def test_invented_wrong_strike_exercise_is_rejected():
    fills=[OptionFill('r','o',STAMP,PUT,D(-2),'TO OPEN',D(5))]
    event=Delivery('e','c',END,FUTURE,PUT.expiration,D(111),'PUT',D(2),D(0),D(-1))
    with pytest.raises(ValueError,match='unique'):replay(fills,[event])


def test_nonzero_delivery_cash_without_offset_is_rejected():
    fills=[OptionFill('r','o',STAMP,PUT,D(-2),'TO OPEN',D(5))]
    event=Delivery('e','c',END,FUTURE,PUT.expiration,PUT.strike,'PUT',D(2),D(-1),D(-1))
    with pytest.raises(ValueError,match='P/L'):replay(fills,[event])


@pytest.mark.parametrize('change', ['orphan','missing','bad_pair','fee_credit','unknown'])
def test_ledger_pairing_and_unknown_activity_fail(tmp_path,change):
    rows=[r.copy() for r in LEDGER]
    if change=='orphan':rows.pop(3)
    if change=='missing':rows.pop(4)
    if change=='bad_pair':rows[4][2]='14:00:02'
    if change=='fee_credit':rows[3][6]='1';rows[3][7]='2'
    if change=='unknown':rows[1][5]='Unrecognized futures cash activity'
    source=ThinkorswimSourceReader().read(str(write_source(tmp_path,ledger=rows)))
    with pytest.raises(ValueError):read_ledger(source,2026,10)


def test_wrong_premium_and_duplicate_execution_rejected(tmp_path):
    rows=[r.copy() for r in FILLS];rows[0][10]='5.10'
    with pytest.raises(ValueError,match='premium cash'):preview(write_source(tmp_path,fills=rows),statement(),year=2026,month=10)
    rows=[*FILLS,FILLS[0],FILLS[1]]
    with pytest.raises(ValueError):preview(write_source(tmp_path,fills=rows),statement(),year=2026,month=10)


@pytest.mark.parametrize('old,new', [('110','111'),('495.00','496.00'),('EXER/ASSN','UNKNOWN'),('OCT 30, 2026','SEP 30, 2026')])
def test_statement_activity_or_period_tampering_withholds_result(tmp_path,old,new):
    with pytest.raises(ValueError):preview(write_source(tmp_path),statement().replace(old,new),year=2026,month=10)


def test_pdf_memo_rows_do_not_double_count_cash():
    text=statement()+'\n'+pdf_row('10/30/26',2,2,'PUT OCT 26 IMM EOM M S&P 100','MEMO P&L','0')
    assert len(read_activity(text))==8


def test_missing_pdf_header_or_unsupported_currency_fails():
    with pytest.raises(ValueError):read_activity(statement().replace(PDF_HEADER,''))
    with pytest.raises(ValueError):read_activity(statement().replace(' US',' EU'))


def test_option_partial_fills_remain_distinct_orders_with_equal_timestamp(tmp_path):
    rows=[r.copy() for r in FILLS]
    rows[0][4]='-1';rows[1][4]='+1'
    source=ThinkorswimSourceReader().read(str(write_source(tmp_path,fills=rows+rows)))
    result=read_fills(source,2026,10)
    assert len(result)==4 and len({f.row_id for f in result})==4
    assert len({f.order_id for f in result})==2
    assert sum((f.cash for f in result),D(0))==200


def test_duplicate_source_identity_is_not_a_second_execution():
    fill=OptionFill('r','o',STAMP,PUT,D(-2),'TO OPEN',D(5))
    with pytest.raises(ValueError,match='Duplicate'):replay([fill,fill],[])


@pytest.mark.parametrize('old,new', [('DEC 26','DEC 25'),('2           2','3           3')])
def test_pdf_offset_contract_and_quantities_are_verified(tmp_path,old,new):
    text=statement()
    if old=='2           2':
        # Change both P/L and fee row quantities without changing any money.
        lines=text.splitlines()
        for i,line in enumerate(lines):
            if 'EMINI S&P 500' in line:
                lines[i]=line[:11]+f'{3:>12}{3:>12}'+line[35:]
        text='\n'.join(lines)
    else:text=text.replace(old,new)
    with pytest.raises(ValueError):preview(write_source(tmp_path),text,year=2026,month=10)


def test_mixed_product_order_and_post_expiry_fill_rejected(tmp_path):
    rows=[r.copy() for r in FILLS];rows[1][6]='SPY';rows[1][7]='30 OCT 26'
    with pytest.raises(ValueError,match='Mixed'):
        read_fills(ThinkorswimSourceReader().read(str(write_source(tmp_path,fills=rows))),2026,10)
    rows=[r.copy() for r in FILLS];rows[0][1]='10/31/26 10:00:00'
    with pytest.raises(ValueError,match='post-expiration'):
        read_fills(ThinkorswimSourceReader().read(str(write_source(tmp_path,fills=rows))),2026,10)


def test_unknown_or_wrongly_signed_cash_sweep_rejected(tmp_path):
    rows=[r.copy() for r in LEDGER];rows[2][5]='Cash sweep to equities account'
    source=ThinkorswimSourceReader().read(str(write_source(tmp_path,ledger=rows)))
    with pytest.raises(ValueError,match='sweep'):read_ledger(source,2026,10)


def test_cli_missing_files_fails_with_json_not_publication_authority(tmp_path):
    result=subprocess.run([sys.executable,'-m','campaigniq.futures_accounting',
        '--trade-history',str(tmp_path/'absent.csv'),'--futures-statement',str(tmp_path/'absent.pdf'),
        '--year','2026','--month','10','--development-only'],capture_output=True,text=True)
    assert result.returncode==1 and json.loads(result.stdout)['publication_authorized'] is False


def test_source_mutation_during_verification_withholds_preview(tmp_path,monkeypatch):
    import campaigniq.futures_accounting as module
    path=write_source(tmp_path)
    real=module.verify_activity
    def change(*args,**kwargs):
        result=real(*args,**kwargs);path.write_text(path.read_text()+'\nchanged\n');return result
    monkeypatch.setattr(module,'verify_activity',change)
    with pytest.raises(ValueError,match='Source changed'):preview(path,statement(),year=2026,month=10)
