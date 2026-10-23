"""September mixed NFLX strike units, Forex date layout, and visible blockers."""
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
import pytest
from campaigniq.import_pipeline import PeriodImportPipeline
from campaigniq.importers.schwab.pre_split_contract_evidence import corroborate_pre_split_contracts
from campaigniq.forex_opening_statement import read_forex_opening_statement, attach_forex_opening, verify_forex_ending
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.lot import Lot
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.import_contract import MonthlyInputRole as Role

START,END=date(2025,9,1),date(2025,9,30)
CSV='Account Statement test\n\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n\nAccount Trade History\n,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type\n,9/4/25 09:40:19,VERTICAL,SELL,-5,TO OPEN,NFLX,17 OCT 25,1165,PUT,25.22,3.50,LMT\n,,,BUY,+5,TO CLOSE,NFLX,17 OCT 25,1150,PUT,21.72,CREDIT,\n,9/9/25 11:16:00,VERTICAL,SELL,-5,TO OPEN,NFLX,17 OCT 25,1195,PUT,29.92,7.85,LMT\n,,,BUY,+5,TO CLOSE,NFLX,17 OCT 25,1165,PUT,22.07,CREDIT,\n,9/26/25 09:46:54,DIAGONAL,SELL,-5,TO OPEN,NFLX,21 NOV 25,111.5,PUT,28.76,4.97,LMT\n,,,BUY,+5,TO CLOSE,NFLX,17 OCT 25,1195,PUT,23.79,CREDIT,\n\nForex Statements\n,Date,Time,Type,Ref #,Description,Commissions & Fees,Amount,Amount(USD),Balance\n\n'
TEXT='Statement Period\nSeptember 1-30, 2025\nTransaction Details\n09/05 Sale          Short Sale      NFLX           PUT NETFLIX INC            $1165        EXP               (5.0000)       25.2200          3.31      12,606.69\n                                     10/17/2025     10/17/25\n                                     1165.00 P      Commission $3.25; Industry Fee $0.06\n09/05 Purchase       Cover Short     NFLX            PUT NETFLIX INC            $1150        EXP                 5.0000        21.7200          3.30     (10,863.30)     2,753.39(ST)\n                                     10/17/2025      10/17/25\n                                     1150.00 P       Commission $3.25; Industry Fee $0.05\n09/10 Sale          Short Sale       NFLX            PUT NETFLIX INC            $1195       EXP            (5.0000)       29.9200          3.31      14,956.69\n                                      10/17/2025      10/17/25\n                                      1195.00 P       Commission $3.25; Industry Fee $0.06\n09/10 Purchase      Cover Short      NFLX            PUT NETFLIX INC            $1165       EXP             5.0000        22.0700          3.30     (11,038.30)     1,568.39(ST)\n                                      10/17/2025      10/17/25\n                                      1165.00 P       Commission $3.25; Industry Fee $0.05\n09/29 Sale          Short Sale      NFLX            PUT NETFLIX INC            $1115       EXP            (5.0000)       28.7600          3.31     14,376.69\n                                     11/21/2025      11/21/25\n                                     1115.00 P       Commission $3.25; Industry Fee $0.06\n09/29 Purchase      Cover Short           NFLX                 PUT NETFLIX INC             $1195        EXP                 5.0000          23.7900                3.30   (11,898.30)     3,058.39(ST)\n                                           10/17/2025           10/17/25\n                                           1195.00 P            Commission $3.25; Industry Fee $0.05\n'
FXTEXT="""Customer Account Summary Report since Friday, August 29, 2025 16:00:00 (CST) through Tuesday, September 30, 2025 16:00:00 (CST)
Monthly Equity Summary
Account Account Currency
1234567 USD
Open Position(s) at End of Month
Floating Profit/Loss Net USD :
\fCustomer Account Summary Report since Friday, August 29, 2025 16:00:00 (CST) through Monday, September 1, 2025 16:00:00 (CST)
Open Position(s)
EUR/USD
TR Details: 8/27/2025 11:57:33 AM Sell (200,000) 1.1609300
EUR/USD - TOTAL POSITION: Sell (200,000) ($2,064.00) $7,026.96
Closed Position(s)
Trade Activity Detail
Financing Report
\fCustomer Account Summary Report since Tuesday, September 16, 2025 16:00:00 (CST) through Wednesday, September 17, 2025 16:00:00 (CST)
Trade Activity Detail
2025-09-17 04:00:17 EUR/USD MARKET B CLOSE 200,000 1.18412 1004170891583 $12.00
Financing Report
"""

@pytest.fixture
def files(tmp_path):
    h=tmp_path/'original.csv';h.write_text(CSV)
    f=tmp_path/'fx.csv'
    f.write_text('Account Statement test\n\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n\nAccount Trade History\nNotes,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type\n\nForex Statements\n,Date,Time,Type,Ref #,Description,Commissions & Fees,Amount,Amount(USD),Balance\n,9/17/25,02:00:17,TRD,="1004170891583","BOT +200,000 EUR/USD @1.18412",--,"-4,638.00","($4,638.00)","$6,427.15"\n\n')
    report=tmp_path/'report.csv';report.write_text('Transaction Report since Aug 31, 2025 through Sep 30, 2025\n"Account (USD): 1234567, Base currency: (USD)"\nMTD Settled PL,$0.00\nMTD Fee,$0.00\n')
    return h,f,report


def trades(files):return tuple(PeriodImportPipeline()._read_trades(files[0],start=START,end=END))
def repair(t,text=TEXT):return corroborate_pre_split_contracts(t,[text.splitlines()],start=START,end=END)
def fx(files,text=FXTEXT):return read_forex_opening_statement(text,trade_history=files[1],transaction_report=files[2],period_end=END)


def test_only_one_of_six_filled_legs_changes(files):
    t=trades(files);before=files[0].read_bytes();u=repair(t)
    pairs=[(a,b) for x,y in zip(t,u) for a,b in zip(x.legs,y.legs)]
    assert len(pairs)==6 and sum(a!=b for a,b in pairs)==1
    for a,b in pairs:
        assert a.executions is b.executions
        if a!=b:assert b==replace(a,contract=replace(a.contract,strike=Decimal(1115)))
        else:assert a.contract.strike in {Decimal(1150),Decimal(1165),Decimal(1195)}
    assert files[0].read_bytes()==before
    assert repair(u)==u


@pytest.mark.parametrize('old,new',[
    ('$1115','$1116'),('1115.00 P','1115.00 C'),('11/21/2025','11/22/2025'),
    ('28.7600','28.7700'),('14,376.69','14,376.68'),
    ('09/29 Sale','09/30 Sale'),('September 1-30, 2025','October 1-31, 2025'),
    ('(5.0000)','(4.0000)'),
])
def test_conflicting_statement_cannot_authorize_september_repair(files,old,new):
    assert old in TEXT
    with pytest.raises(ValueError):repair(trades(files),TEXT.replace(old,new,1))


def test_incomplete_or_duplicate_evidence_rejected(files):
    with pytest.raises(ValueError):repair(trades(files)[1:])
    with pytest.raises(ValueError):repair(trades(files),TEXT+'\n'+'\n'.join(TEXT.splitlines()[3:6]))


def test_real_111_point_5_contract_is_not_forced_to_1115(files):
    text=TEXT.replace('$1115','$111.5').replace('1115.00 P','111.50 P')
    assert repair(trades(files),text)==trades(files)


def test_no_statement_no_inferred_repair(files):
    assert repair(trades(files),'Positions - Options\nNFLX $1115')==trades(files)


def test_september_pipeline_ends_exactly_five_1115_puts(files):
    first=OptionContract('NFLX',date(2025,10,17),Decimal(1150),OptionType.PUT)
    last=OptionContract('NFLX',date(2025,11,21),Decimal(1115),OptionType.PUT)
    book=LotBook();lot=Lot('original',first,Decimal(-5),datetime(2025,8,29),None,campaign_id='NFLX-CANON');book.seed(lot)
    result=PeriodImportPipeline().run(period_start=START,period_end=END,thinkorswim_trade_history=files[0],carried_opening_lot_book=book,assignment_lines=(TEXT.splitlines(),),campaign_namespace='2025-09')
    assert book.lots(first)==(lot,)
    assert result.ending_lot_book.instruments()==(last,)
    assert sum(l.quantity for l in result.ending_lot_book.lots(last))==-5
    assert not result.position_events
    from campaigniq.persistence.position_journal import capture_position_journal
    assert capture_position_journal(result,START)


@pytest.mark.parametrize('value',['8/27/2025','08/27/2025','8/7/2025','08/07/2025'])
def test_supported_padded_and_unpadded_opening_dates(files,value):
    e=fx(files,FXTEXT.replace('8/27/2025',value))
    assert e.opening==(('EUR/USD',Decimal(-200000)),) and e.closing==()


@pytest.mark.parametrize('value',['9/1/2025','09/01/2025','8/32/2025','8/27/25','2025-08-27','8/27/2025bad','nonsense'])
def test_invalid_or_in_period_opening_dates_still_rejected(files,value):
    with pytest.raises(ValueError):fx(files,FXTEXT.replace('8/27/2025',value))


def test_malformed_additional_detail_not_silently_ignored(files):
    with pytest.raises(ValueError):fx(files,FXTEXT.replace('Closed Position(s)','TR Details: bad-date\nClosed Position(s)'))


def test_forex_pipeline_closes_verified_short_without_repairing_money_report(files):
    before=[p.read_bytes() for p in files]
    e=fx(files);original=LotBook();b=attach_forex_opening(original,e,period_start=START,authoritative=False)
    r=PeriodImportPipeline().run(period_start=START,period_end=END,thinkorswim_trade_history=files[1],carried_opening_lot_book=b,forex_transaction_report=files[2],campaign_namespace='2025-09')
    verify_forex_ending(r.ending_lot_book,e)
    assert not r.ending_lot_book.instruments() and not original.instruments()
    assert r.forex_transaction_report.settlements==()  # Unchanged CSV does not contain a settlement row.
    assert before==[p.read_bytes() for p in files]


@pytest.mark.parametrize('valid',[True,False])
def test_monthly_forex_validation_is_visible_and_keeps_its_severity(monkeypatch,valid):
    from campaigniq.ui import dashboard as d
    from campaigniq.import_validation import MonthlyInputValidation
    v=MonthlyInputValidation(role=Role.SCHWAB_FOREX_MONTHLY_STATEMENT,valid=valid,message='specific opening evidence result')
    other=SimpleNamespace(valid=True,message='other input passed',record_count=0)
    preflight=SimpleNamespace(validations=(v,),validation_for=lambda role:other)
    messages=[]
    fake=SimpleNamespace(markdown=lambda *a:None,caption=lambda *a:None,
        success=lambda message:messages.append(('success',message)),error=lambda message:messages.append(('error',message)),info=lambda message:messages.append(('info',message)))
    monkeypatch.setattr(d,'st',fake)
    d._show_monthly_preflight(preflight)
    assert [('success' if valid else 'error','**Schwab Forex Monthly Statement:** specific opening evidence result')] == [m for m in messages if 'specific opening evidence result' in m[1]]
