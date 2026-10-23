"""October NFLX literal broker rows and negative corroboration controls."""
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
import pytest
from campaigniq.import_pipeline import PeriodImportPipeline
from campaigniq.importers.schwab.pre_split_contract_evidence import (
    corroborate_pre_split_contracts, START, END,
)
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook

CSV = 'Account Statement test\n\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n\nAccount Trade History\n,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type\n,10/9/25 08:51:24,VERTICAL,SELL,-5,TO OPEN,NFLX,21 NOV 25,114,PUT,27.30,6.40,LMT\n,,,BUY,+5,TO CLOSE,NFLX,21 NOV 25,111.5,PUT,20.90,CREDIT,\n,10/10/25 08:20:26,VERTICAL,SELL,-1,TO OPEN,NFLX,21 NOV 25,115.5,PUT,26.40,3.91,LMT\n,,,BUY,+1,TO CLOSE,NFLX,21 NOV 25,114,PUT,22.49,CREDIT,\n,10/10/25 08:20:26,VERTICAL,SELL,-3,TO OPEN,NFLX,21 NOV 25,115.5,PUT,26.39,3.90,LMT\n,,,BUY,+3,TO CLOSE,NFLX,21 NOV 25,114,PUT,22.49,CREDIT,\n,10/10/25 08:20:26,VERTICAL,SELL,-1,TO OPEN,NFLX,21 NOV 25,115.5,PUT,26.39,3.89,LMT\n,,,BUY,+1,TO CLOSE,NFLX,21 NOV 25,114,PUT,22.50,CREDIT,\n,10/30/25 12:30:27,DIAGONAL,SELL,-5,TO OPEN,NFLX,19 DEC 25,114,PUT,65.53,1.27,LMT\n,,,BUY,+5,TO CLOSE,NFLX,21 NOV 25,115.5,PUT,64.26,CREDIT,\n\nForex Statements\n,Date,Time,Type,Ref #,Description,Commissions & Fees,Amount,Amount(USD),Balance\n\n'
TEXT = 'Statement Period\nOctober 1-31, 2025\nTransaction Details\n10/10 Sale          Short Sale      NFLX            PUT NETFLIX INC            $1140       EXP            (5.0000)       27.3000          3.31      13,646.69\n                                     11/21/2025      11/21/25\n                                     1140.00 P       Commission $3.25; Industry Fee $0.06\n10/10 Purchase      Cover Short     NFLX            PUT NETFLIX INC            $1115       EXP             5.0000        20.9000          3.30     (10,453.30)     3,923.39(ST)\n                                     11/21/2025      11/21/25\n                                     1115.00 P       Commission $3.25; Industry Fee $0.05\n10/14 Sale           Short Sale      NFLX            PUT NETFLIX INC            $1155        EXP                (5.0000)       26.3920          3.31      13,192.69\n                                     11/21/2025      11/21/25\n                                     1155.00 P       Commission $3.25; Industry Fee $0.06\n10/14 Purchase      Cover Short     NFLX            PUT NETFLIX INC            $1140        EXP                 5.0000        22.4920          3.30     (11,249.30)     2,397.39(ST)\n                                     11/21/2025      11/21/25\n                                     1140.00 P       Commission $3.25; Industry Fee $0.05\n10/31 Sale          Short Sale      NFLX             PUT NETFLIX INC            $1140        EXP                (5.0000)       65.5300          3.31     32,761.69\n                                     12/19/2025       12/19/25\n                                     1140.00 P        Commission $3.25; Industry Fee $0.06\n10/31 Purchase          Cover Short           NFLX                PUT NETFLIX INC              $1155          EXP               5.0000            64.2600              3.30           (32,133.30)           (18,940.61)(ST)\n                                              11/21/2025          11/21/25\n                                              1155.00 P           Commission $3.25; Industry Fee $0.05\n'


@pytest.fixture
def history(tmp_path):
    p=tmp_path/'original.csv';p.write_text(CSV)
    return p


def read(history):
    return tuple(PeriodImportPipeline()._read_trades(history,start=START,end=END))


def repair(trades, text=TEXT):
    return corroborate_pre_split_contracts(trades,[text.splitlines()],start=START,end=END)


def test_literal_weighted_fills_correct_contracts_only(history):
    original=read(history);data=history.read_bytes()
    result=repair(original)
    assert len(result)==len(original)
    assert {l.instrument.strike for t in result for l in t.legs}=={Decimal(1115),Decimal(1140),Decimal(1155)}
    for a,b in zip(original,result):
        for old,new in zip(a.legs,b.legs):
            assert new==replace(old,contract=replace(old.contract,strike=old.contract.strike*10))
            assert new.executions is old.executions
    assert history.read_bytes()==data
    assert {l.instrument.strike for t in original for l in t.legs}=={Decimal('111.5'),Decimal(114),Decimal('115.5')}


def test_idempotent_with_already_corrected_contracts(history):
    result=repair(read(history))
    assert repair(result)==result


def test_repeated_whole_statement_is_one_source(history):
    trades=read(history);lines=TEXT.splitlines()
    assert corroborate_pre_split_contracts(trades,[lines,lines],start=START,end=END)==repair(trades)


@pytest.mark.parametrize('old,new',[
    ('27.3000','27.3100'),('13,646.69','13,646.68'),
    ('(5.0000)','(4.0000)'),('$1140','$1141'),
    ('1140.00 P','1140.00 C'),('11/21/2025','11/22/2025'),
    ('11/21/25','11/22/25'),('10/10 Sale','10/09 Sale'),
    ('Short Sale','Cover Short'),('3.31','3.32'),
    ('October 1-31, 2025','September 1-30, 2025'),
    ('Statement Period','Unknown Period'),('26.3920','26.3900'),
])
def test_changed_statement_evidence_cannot_authorize_repair(history,old,new):
    assert old in TEXT
    with pytest.raises(ValueError):repair(read(history),TEXT.replace(old,new,1))


@pytest.mark.parametrize('field,value',[
    ('quantity',Decimal(-4)),('execution_price',Decimal('27.31')),
    ('executed_at',datetime(2025,10,8,8,51,24)),
])
def test_changed_execution_facts_block(history,field,value):
    trades=list(read(history));leg=trades[0].legs[0]
    trades[0]=replace(trades[0],legs=(replace(leg,executions=(replace(leg.executions[0],**{field:value}),)),*trades[0].legs[1:]))
    with pytest.raises(ValueError):repair(trades)


def test_missing_filled_leg_blocks(history):
    with pytest.raises(ValueError):repair(read(history)[1:])


def test_missing_statement_leg_blocks(history):
    lines=TEXT.splitlines()
    with pytest.raises(ValueError):repair(read(history),'\n'.join(lines[:3]+lines[6:]))


def test_duplicate_statement_transaction_blocks(history):
    lines=TEXT.splitlines()
    with pytest.raises(ValueError,match='Duplicate'):repair(read(history),TEXT+'\n'+'\n'.join(lines[3:6]))


def test_conflicting_whole_statements_block(history):
    with pytest.raises(ValueError):
        corroborate_pre_split_contracts(read(history),[TEXT.splitlines(),(TEXT+'\nextra source').splitlines()],start=START,end=END)


def test_no_statement_does_not_infer_contract_repair(history):
    trades=read(history)
    assert corroborate_pre_split_contracts(trades,[],start=START,end=END)==trades
    assert repair(trades,'Positions - Options\nNFLX 1140 PUT -5')==trades


@pytest.mark.parametrize('start,end',[(date(2025,9,1),date(2025,9,30)),(date(2025,11,1),date(2025,11,30)),(date(2025,10,9),END)])
def test_other_periods_never_receive_october_repair(history,start,end):
    trades=read(history)
    assert corroborate_pre_split_contracts(trades,[TEXT.splitlines()],start=start,end=end)==trades


def test_other_symbols_unchanged(history):
    trades=tuple(replace(t,legs=tuple(replace(l,contract=replace(l.contract,underlying='OTHER')) for l in t.legs)) for t in read(history))
    assert repair(trades)==trades


def test_real_lower_strike_contract_is_not_multiplied(history):
    # A corroborated actual $114 put is already in broker units.
    result=repair(read(history))
    trades=tuple(replace(t,legs=tuple(replace(l,contract=replace(l.contract,strike=l.contract.strike/10)) for l in t.legs)) for t in result)
    text=TEXT.replace('$1140','$114').replace('1140.00 P','114.00 P').replace('$1115','$111.5').replace('1115.00 P','111.50 P').replace('$1155','$115.5').replace('1155.00 P','115.50 P')
    assert repair(trades,text)==trades


def test_october_pipeline_closes_original_lots_and_ends_five_actual_1140_puts(history):
    old=OptionContract('NFLX',date(2025,11,21),Decimal(1115),OptionType.PUT)
    ending=OptionContract('NFLX',date(2025,12,19),Decimal(1140),OptionType.PUT)
    book=LotBook();lot=Lot('original-lot',old,Decimal(-5),datetime(2025,9,1),Decimal(14376.69),'broker','CANON-NFLX')
    book.seed(lot)
    result=PeriodImportPipeline().run(period_start=START,period_end=END,thinkorswim_trade_history=history,
        carried_opening_lot_book=book,assignment_lines=(TEXT.splitlines(),),expiration_lines=(TEXT.splitlines(),),campaign_namespace='2025-10')
    assert book.lots(old)==(lot,)
    assert not result.ending_lot_book.lots(old)
    assert result.ending_lot_book.instruments()==(ending,)
    assert sum((l.quantity for l in result.ending_lot_book.lots(ending)),Decimal(0))==-5
    from campaigniq.persistence.position_journal import capture_position_journal
    assert capture_position_journal(result,START)
    assert not result.position_events  # No manufactured split or assignment.
