from copy import deepcopy
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

import pytest
from campaigniq.broker_account_binding import BrokerBinding
from campaigniq.forex_daily_financial_candidate import calculate, canonical
from campaigniq.forex_daily_rehearsal_sources import reports
from campaigniq.forex_cash_rehearsal_sources import sources
from campaigniq.forex_inventory_rehearsal_sources import predecessor
from campaigniq.persistence.lot_book_store import deserialize_lot_book

KNOWN = {'forex':BrokerBinding.create('forex','8642097')}


def bundle(rows):
    return [dict(name=n,text=t,pdf_sha256=sha256(t.encode()).hexdigest()) for n,t in rows]


@pytest.fixture
def data(tmp_path):
    uploads, ledger, _ = sources()
    cash = tmp_path/'cash.csv'; cash.write_bytes(ledger.content)
    report = tmp_path/'report.csv'; report.write_bytes(uploads['schwab_forex_transaction_report'].content)
    return dict(reports=bundle(reports()),known=KNOWN,cash_ledger=cash,transaction_report=report,
        opening_book=deserialize_lot_book(predecessor()).lot_book)


def run(data):
    args=dict(data); rows=args.pop('reports')
    return calculate(rows,**args)


def test_missing_close_exact_lot_and_original_controls_are_retained(data):
    originals=[data[k].read_bytes() for k in ('cash_ledger','transaction_report')]
    result=run(data)
    assert result['totals']==dict(opening_cash_usd='1000.00',trading_usd='-10.00',financing_usd='-1.00',ending_cash_usd='989.00',net_cash_change_usd='-11.00')
    assert result['missing_csv_execution_references']==['1001']
    assert result['closes'][0]['csv_settlement_at'] is None
    assert result['closes'][0]['csv_pl_usd'] is None
    assert result['original_csv_mtd_minus_candidate_usd']=='1.00'
    assert result['raw_csv_controls']['mtd_settled_pl_usd']=='-9.00'
    assert result['close_allocations']==[dict(reference='1001',lot_id='synthetic-forex-opening',campaign_id='synthetic-existing-forex-campaign',quantity='50000')]
    assert result['ending_quantities']=={} and result['authoritative_opening_matches']
    assert all(Decimal(v)==0 for v in result['equation_deltas_usd'].values())
    assert not result['full_independent_daily_coverage']
    assert all(result[k] is False for k in ('original_discrepancies_resolved','policy_approved','financial_state_written','publication_authorized'))
    assert run(data)==result
    assert originals==[data[k].read_bytes() for k in ('cash_ledger','transaction_report')]
    assert data['opening_book'].lots(data['opening_book'].instruments()[0])[0].quantity==-50000


def pending_cutoff(data):
    # Small, wholly synthetic reproduction of a prior-calendar financing row,
    # a three-cent cash/export difference, and next-session cash settlement.
    rows=[]; base=reports()
    opening=1001.03
    for i,(name,text) in enumerate(base):
        start=text.split('Equity Summary')[0]
        account='Account Account Currency\n8642097 USD\nStatement From Statement To\n'
        begin='1001.03' if i==0 else '1000.03'
        end='1000.03' if i<4 else '988.00'
        finance='($1.00)' if i==0 else ('($2.00)' if i==4 else '$0.00')
        settled='($10.03)' if i==4 else '$0.00'
        pending='($10.03)' if i==3 else '$0.00'
        begin_eq='990.00' if i==4 else begin
        end_eq='990.00' if i==3 else end
        values=('Equity Summary\nBeginning Cash Balance $'+begin+'\nBeginning Account Equity $'+begin_eq+'\n'
            'Deposits $0.00\nWithdrawals $0.00\nAdjustments $0.00\nFinancing '+finance+'\n'
            'Settled Profit/Loss '+settled+'\nUnrealized Profit/Loss '+pending+'\nFloating Profit/Loss $0.00\n'
            'Ending Cash Balance $'+end+'\nEnding Account Equity $'+end_eq+'\n')
        sections=('Profit/Loss Summary\nCurrency Pair Settled Profit/Loss Unrealized Profit/Loss Floating Profit/Loss\n'
            'USD/JPY '+settled+' '+pending+' $0.00\nOpen Position(s)\n'
            'Currency Pair Buy/Sell Quantity Trade Price Floating Profit/Loss USD Margin Requirement USD\n')
        if i<3:
            sections+='USD/JPY\nTR Details: 9/25/2026 3:47:16 PM Sell (50,000) 157.2060000\nUSD/JPY - TOTAL POSITION: Sell (50,000) $0.00 $2500.00\n'
        sections+='Floating Profit/Loss Net USD :\nClosed Position(s)\n'
        if i==3:sections+='USD/JPY ($10.03)\n'
        sections+='Trade Activity Detail\n'
        if i==3:sections+='2026-10-06 21:04:08 USD/JPY STOP B CLOSE 50,000 158.505 1001 $0.00\n'
        sections+='Financing Report\n'
        if i==0:sections+='2026-09-30 17:00:00 USD/JPY (50,000) 158.505 2000 $0.01 ($1.00)\n'
        if i==4:sections+='2026-10-06 17:00:00 USD/JPY (50,000) 158.505 1002 $0.01 ($2.00)\n'
        sections+='Conversion Report\n'
        rows.append((name,start+values+account+sections))
    cash=data['cash_ledger'].read_text()
    cash=cash.replace(',9/30/26,23:00:00,BAL,--,Opening cash,--,--,--,$1000.00',
        ',9/29/26,23:00:00,BAL,--,Opening cash,--,--,--,$1001.00\n'
        ',9/30/26,15:00:00,DOI,="2000",Interest charged,--,-1,($1.00),$1000.00')
    cash=cash.replace('Interest charged,--,-1,($1.00),$989.00','Interest charged,--,-2,($2.00),$988.00').replace('$989.00','$988.00')
    data['cash_ledger'].write_text(cash);data['reports']=bundle(rows)


def test_pending_settlement_and_calendar_cutoff_differences_balance_exactly(data):
    pending_cutoff(data);r=run(data)
    assert r['totals']['trading_usd']=='-10.03'
    assert r['totals']['financing_usd']=='-3.00'
    assert r['calendar_comparison']==dict(original_opening_cash_usd='1000.00',original_trade_cash_usd='-10.00',
        original_financing_usd='-2.00',prior_calendar_financing_in_daily_sessions_usd='-1.00',
        daily_derived_calendar_opening_usd='1000.03',derived_opening_minus_export_usd='0.03',daily_closes_minus_export_usd='-0.03')
    assert sum(Decimal(row['new_close_pl_usd']) for row in r['settlement_bridges'])==Decimal('-10.03')
    assert r['closes'][0]['reporting_boundary']=='2026-10-06'
    assert r['closes'][0]['csv_settlement_at'] is None


@pytest.mark.parametrize('change',['pair_total','pending_table','financing_amount','financing_id','cash_tail','csv_execution','fee','nonfinite','multiple_closes'])
def test_financial_conflicts_withhold_candidate_without_writes(data,change):
    if change=='pending_table':pending_cutoff(data)
    rows=deepcopy(data['reports'])
    if change=='pair_total':rows[-1]['text']=rows[-1]['text'].replace('USD/JPY ($10.00) $0.00 $0.00','USD/JPY ($9.99) $0.00 $0.00')
    elif change=='pending_table':rows[-2]['text']=rows[-2]['text'].replace('USD/JPY ($10.03)\n','USD/JPY ($10.02)\n')
    elif change=='financing_id':rows[-1]['text']=rows[-1]['text'].replace('158.505 1002','158.505 1009')
    elif change=='financing_amount':data['cash_ledger'].write_text(data['cash_ledger'].read_text().replace('($1.00)','$0.00'))
    elif change=='cash_tail':data['cash_ledger'].write_text(data['cash_ledger'].read_text().replace('Closing cash,--,--,--,$989.00','Closing cash,--,--,--,$988.00'))
    elif change=='csv_execution':
        with data['transaction_report'].open('a') as f:f.write('1009,"Oct 6, 2026 21:04:08","Oct 7, 2026 17:00:00",settlement,USD/JPY,Buy,158.505,50000,-7925250,0,,"-10 USD",0\n')
    elif change=='fee':data['transaction_report'].write_text(data['transaction_report'].read_text().replace('USD:",0.00','USD:",1.00'))
    elif change=='nonfinite':rows[-1]['text']=rows[-1]['text'].replace('($10.00)','NaN')
    elif change=='multiple_closes':
        rows[-1]['text']=rows[-1]['text'].replace('50,000 158.505 1001 $0.00',
            '25,000 158.505 1001 $0.00\n2026-10-06 21:04:09 USD/JPY STOP B CLOSE 25,000 158.505 1003 $0.00')
        cash=data['cash_ledger'].read_text().replace('"BOT +50,000 USD/JPY @158.505",--,-1000,($10.00),$990.00',
            '"BOT +25,000 USD/JPY @158.505",--,-500,($5.00),$995.00\n'
            ',10/6/26,19:04:09,TRD,="1003","BOT +25,000 USD/JPY @158.505",--,-500,($5.00),$990.00')
        data['cash_ledger'].write_text(cash)
    data['reports']=rows
    before=[data[k].read_bytes() for k in ('cash_ledger','transaction_report')]
    with pytest.raises(ValueError):run(data)
    assert before==[data[k].read_bytes() for k in ('cash_ledger','transaction_report')]


def test_zero_profit_close_remains_an_explicit_execution(data):
    data['cash_ledger'].write_text(data['cash_ledger'].read_text().replace('($10.00)','$0.00').replace('$990.00','$1000.00').replace('$989.00','$999.00'))
    for r in data['reports']:
        r['text']=r['text'].replace('($10.00)','$0.00').replace('$989.00','$999.00')
    r=run(data)
    assert len(r['closes'])==1 and Decimal(r['closes'][0]['candidate_pl_usd'])==0


def test_no_canonical_book_is_never_claimed_as_verified(data):
    data['opening_book']=None;r=run(data)
    assert not r['authoritative_opening_matches'] and not r['close_allocations']
    assert not r['publication_authorized']


@pytest.mark.parametrize('same_reference,same_amount,classified',[(True,True,True),(False,True,False),(True,False,False)])
def test_securities_financing_classification_requires_exact_reference_and_amount(data,same_reference,same_amount,classified):
    with data['transaction_report'].open('a') as f:
        f.write('9001,"Oct 5, 2026 10:00:00","Oct 5, 2026 10:00:00",financing,US$,,,,,,,,,5.00,5.00 USD\n')
    with data['cash_ledger'].open('a') as f:
        f.write('\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n'
            '10/5/26,10:00:00,DIV,'+('9001' if same_reference else '9002')+',Synthetic dividend,--,--,'+('5.00' if same_amount else '4.99')+',100.00\n\n')
    result=run(data)
    assert result['all_original_financing_rows_classified'] is classified
    row=result['raw_csv_financing_classification'][0]
    assert (row['classification']=='matched_securities_cash_reference_and_amount') is classified
    assert result['raw_csv_controls']['financing'][0]['instrument']=='US$'
    assert result['raw_csv_controls']['financing'][0]['financing_usd']=='5.00'


def test_conflicting_original_financing_delivery_amount_withholds_candidate(data):
    with data['transaction_report'].open('a') as f:
        f.write('1002,"Oct 6, 2026 17:00:00","Oct 6, 2026 17:00:00",financing,USD$FX,,,,,,,,,-1.01,-1.00 USD\n')
    with pytest.raises(ValueError,match='delivery'):run(data)
