"""Literal November evidence plus split and holiday failure controls."""
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import pytest

from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event import PositionEvent, PositionChange
from campaigniq.domain.position_event_kind import PositionEventKind as Kind
from campaigniq.domain.position_event_reconciler import PositionEventReconciler
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.schwab.option_split_reader import read_option_split_events

OLD = OptionContract('NFLX',date(2025,12,19),Decimal('1140'),OptionType.PUT)
NEW = OptionContract('NFLX',date(2025,12,19),Decimal('114'),OptionType.PUT)
SPLIT = '11/17 Purchase                          UNH             UNITEDHEALTH GROUP INC                                   100.0000      323.2100                    (32,321.00)\n       Other         Option Fwd Split   NFLX            PUT NETFLIX INC            $114        EXP              (45.0000)\n       Activity                         12/19/2025      12/19/25\n                                        114.00 P'
CLOSING = 'NFLX     PUT NETFLIX INC ,              (50.0000) S    7.20650           (36,032.50)   (32,761.69)       (3,270.81)\n12/19/20 $114  EXP 12/19/25\n25\n114.00 P'
TEXT = 'Statement Period\nNovember 1-30, 2025\nPositions - Options\n'+CLOSING+'\nTransaction Details\n'+SPLIT+'\n'


def opening():
    b=LotBook()
    b.seed(Lot('original-nflx-lot',OLD,Decimal('-5'),datetime(2025,10,20),Decimal('32761.69'),'broker-source','CANON-NFLX'))
    return b


def event(text=TEXT,book=None):
    events=read_option_split_events(text.splitlines(),opening_lot_book=opening() if book is None else book,
        trades=(),start=date(2025,11,1),end=date(2025,11,30))
    assert len(events)==1
    return events[0]


def test_literal_nflx_statement_split_matches_occ_and_opening():
    e=event()
    assert e.kind is Kind.SPLIT
    assert e.occurred_at==datetime(2025,11,17)
    assert e.changes==(PositionChange(OLD,Decimal(5)),PositionChange(NEW,Decimal(-50)))


def test_split_preserves_total_basis_lot_id_opened_at_and_campaign():
    b=opening();original=b.lots(OLD)[0]
    assert b.apply_event(event())==()
    assert not b.lots(OLD)
    assert b.lots(NEW)==(replace(original,instrument=NEW,quantity=Decimal('-50')),)
    assert b.lots(NEW)[0].basis_total==original.basis_total


def test_split_closing_allocations_retain_total_broker_basis():
    b=opening();b.apply_event(event())
    allocations=b.apply_signed_change(instrument=NEW,quantity=Decimal(50),occurred_at=datetime(2025,12,1))
    # Closed-lot basis remains owned by the broker realized report.
    assert all(a.broker_basis is None for a in allocations)
    assert sum((a.quantity for a in allocations),Decimal(0))==Decimal(50)
    assert {a.lot_id for a in allocations}=={'original-nflx-lot'}
    assert {a.campaign_id for a in allocations}=={'CANON-NFLX'}


def test_multiple_lots_preserve_each_identity_and_basis():
    b=LotBook()
    b.seed(Lot('a',OLD,Decimal(-2),datetime(2025,10,1),Decimal(1000),campaign_id='A'))
    b.seed(Lot('b',OLD,Decimal(-3),datetime(2025,10,2),None,campaign_id='B'))
    before=b.lots(OLD)
    b.apply_event(event(book=b))
    assert b.lots(NEW)==tuple(replace(lot,instrument=NEW,quantity=lot.quantity*10) for lot in before)


def test_reader_never_modifies_source_opening_state():
    b=opening();before=b.lots(OLD)
    event(book=b)
    assert b.lots(OLD)==before and not b.lots(NEW)


@pytest.mark.parametrize('old,new',[
    ('11/17 Purchase','11/18 Purchase'),
    ('Option Fwd Split   NFLX','Option Fwd Split   OTHER'),
    ('(45.0000)','(44.0000)'),
    ('(50.0000)','(49.0000)'),
    ('114.00 P','114.01 P'),
    ('114.00 P','114.00 C'),
    ('Activity                         12/19/2025','Activity                         12/20/2025'),
    ('November 1-30, 2025','October 1-31, 2025'),
])
def test_corrupt_split_evidence_is_not_overridden(old,new):
    assert old in TEXT
    with pytest.raises(ValueError):event(TEXT.replace(old,new))


def test_duplicate_split_statement_rows_rejected():
    with pytest.raises(ValueError,match='Duplicate'):
        event(TEXT+'\n'+SPLIT)


def test_closing_snapshot_alone_cannot_infer_split():
    assert read_option_split_events(('Statement Period\nNovember 1-30, 2025\nPositions - Options\n'+CLOSING).splitlines(),
        opening_lot_book=opening(),trades=(),start=date(2025,11,1),end=date(2025,11,30))==()


def test_missing_pre_split_contract_rejected():
    with pytest.raises(ValueError):event(book=LotBook())


def test_already_adjusted_inventory_cannot_be_split_again():
    b=opening();b.apply_event(event())
    with pytest.raises(ValueError):event(book=b)


def test_transformation_cannot_be_applied_twice():
    b=opening();e=event();b.apply_event(e)
    before=b.lots(NEW)
    with pytest.raises(ValueError):b.apply_event(e)
    assert b.lots(NEW)==before


@pytest.mark.parametrize('change',[
    PositionChange(OLD,Decimal(4)),
    PositionChange(OLD,Decimal('NaN')),
])
def test_invalid_split_offsets_leave_book_untouched(change):
    b=opening();before=b.lots(OLD)
    with pytest.raises(ValueError):b.apply_event(replace(event(),changes=(change,event().changes[1])))
    assert b.lots(OLD)==before and not b.lots(NEW)


def test_split_is_not_a_realized_closure():
    from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
    a=RealizedLotAttributor(opening())
    assert a.attribute_campaigns([],[],[event()])==()
    assert a.lot_book.lots(NEW)[0].basis_total==Decimal('32761.69')


def assignment(symbol='TMUS',quantity=500,when=datetime(2025,11,28)):
    option=OptionContract(symbol,date(2025,12,19),Decimal(250),OptionType.PUT)
    return PositionEvent(Kind.ASSIGNMENT,(PositionChange(option,Decimal(quantity)/100),PositionChange(Instrument(symbol),Decimal(quantity))),when)


def delivery(symbol='TMUS',quantity=500,when=datetime(2025,11,26,23,29,24)):
    return PositionEvent(Kind.EXPIRATION,(PositionChange(Instrument(symbol),Decimal(quantity)),),when)


def test_thanksgiving_tmus_delivery_is_counted_once():
    a,d=assignment(),delivery()
    assert PositionEventReconciler().reconcile((a,d))==(a,)


@pytest.mark.parametrize('d',[
    delivery(quantity=499), delivery(quantity=-500), delivery(symbol='OTHER'),
    delivery(when=datetime(2025,11,25)),
])
def test_holiday_does_not_broaden_quantity_symbol_direction_or_date_matching(d):
    assert len(PositionEventReconciler().reconcile((assignment(),d)))==2


def test_nonholiday_two_day_gap_is_not_deduplicated():
    assert len(PositionEventReconciler().reconcile((assignment(when=datetime(2025,11,21)),delivery(when=datetime(2025,11,19)))))==2


def test_weekend_matching_is_preserved():
    a=assignment(when=datetime(2025,11,24));d=delivery(when=datetime(2025,11,21))
    assert PositionEventReconciler().reconcile((a,d))==(a,)


def test_new_split_event_does_not_change_position_event_quantity_rules():
    from campaigniq.domain.position_event_applier import PositionEventApplier
    b=PositionEventApplier()
    b.apply_event(PositionEvent(Kind.EXPIRATION,(PositionChange(OLD,Decimal(-5)),),datetime(2025,10,20)))
    b.apply_event(event())
    assert b.quantity(OLD)==0 and b.quantity(NEW)==-50


def test_split_month_nflx_trades_require_supported_chronology():
    from types import SimpleNamespace
    trade=SimpleNamespace(legs=[SimpleNamespace(instrument=OLD)])
    with pytest.raises(ValueError,match='chronology'):
        read_option_split_events(TEXT.splitlines(),opening_lot_book=opening(),trades=(trade,),start=date(2025,11,1),end=date(2025,11,30))


def test_split_requires_explicit_statement_period():
    with pytest.raises(ValueError,match='period'):
        event(TEXT.replace('Statement Period','Unknown header'))


def test_mixed_sign_lots_are_rejected_without_mutation():
    b=opening();b.seed(Lot('offset',OLD,Decimal(1),datetime(2025,10,21),None))
    original=b.lots(OLD)
    with pytest.raises(ValueError):b.apply_event(event())
    assert b.lots(OLD)==original and not b.lots(NEW)


def test_unrelated_target_contract_cannot_be_replaced():
    b=opening();b.seed(Lot('target',NEW,Decimal(-1),datetime(2025,10,21),None))
    original=b.lots(OLD);target=b.lots(NEW)
    with pytest.raises(ValueError):b.apply_event(event())
    assert b.lots(OLD)==original and b.lots(NEW)==target


def test_combined_november_pipeline_and_journal_preserve_one_delivery_and_split(tmp_path):
    from campaigniq.import_pipeline import PeriodImportPipeline
    from campaigniq.persistence.position_journal import capture_position_journal
    from campaigniq.analytics.period_realized_attributions import attribute_period_realized_pnl
    book=opening()
    tmus=OptionContract('TMUS',date(2025,12,19),Decimal(250),OptionType.PUT)
    book.seed(Lot('tmus-put',tmus,Decimal(-5),datetime(2025,10,1),None,campaign_id='CANON-TMUS'))
    before=book.lots(OLD)
    history=tmp_path/'history.csv'
    history.write_text('Account Statement test\n\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n11/26/25,23:29:24,EXP,="107647982856",BOT 500.0 TMUS UPON T-MOBILE US INC,,,"-125000.00",0\n\nAccount Trade History\nNotes,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type\n\nForex Statements\n,Date,Time,Type,Ref #,Description,Commissions & Fees,Amount,Amount(USD),Balance\n\n')
    assignments=['11/28','Other Activity','Option Assignment','TMUS','12/19/2025 250.00 P','PUT T-MOBILE US INC','5.0000']
    result=PeriodImportPipeline().run(period_start=date(2025,11,1),period_end=date(2025,11,30),thinkorswim_trade_history=history,
        carried_opening_lot_book=book,assignment_lines=(assignments,),expiration_lines=(TEXT.splitlines(),),campaign_namespace='2025-11')
    assert not result.ending_lot_book.lots(OLD)
    assert result.ending_lot_book.lots(NEW)==(replace(before[0],instrument=NEW,quantity=Decimal(-50)),)
    assert sum((l.quantity for l in result.ending_lot_book.lots(Instrument('TMUS'))),Decimal(0))==500
    assert book.lots(OLD)==before and not book.lots(Instrument('TMUS'))
    assert [e.kind for e in result.position_events]==[Kind.SPLIT,Kind.ASSIGNMENT]
    assert attribute_period_realized_pnl(result)==()
    journal=capture_position_journal(result,date(2025,11,1))
    assert journal  # The journal verifies its replay against exact ending lot state.
    assert len(result.lifecycle_transitions)==1  # Assignment only, no fake split roll or exit.
