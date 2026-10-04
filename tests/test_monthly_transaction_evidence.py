from datetime import date, datetime
from decimal import Decimal
import pytest

from campaigniq.importers.schwab.monthly_transaction_evidence import recover_statement_allocations, carry_assignment_deliveries
from campaigniq.importers.schwab.option_assignment_flow import read_option_assignment_events
from campaigniq.domain.position_event import PositionEvent, PositionChange
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.lot import Lot
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.lot_book_period_applier import LotBookPeriodApplier
from campaigniq.persistence.artifact_storage import LocalFilesystemArtifactStorage
from campaigniq.persistence.reconciliation_decision import ReconciliationDecision, PersistedReconciliationMismatch, serialize_reconciliation_decision, reconciliation_decision_key, BOUNDARY_TIMING_EXCEPTION, ACCEPT_TRANSACTION_DERIVED_STATE
from campaigniq.monthly_import_execution import _verified_prior_deliveries


def order_file(tmp_path, *, status="(0) FILLED", qty="+37", price="135.00"):
    path = tmp_path / "history.csv"
    path.write_text('Account Statement\n\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n5/30/26,04:05:52,EXP,1,SOLD -100.0 GS UPON GOLDMAN SACHS GROUP INC,-1.50,,72000,0\n\nAccount Order History\nNotes,,Time Placed,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,PRICE,,TIF,Status\n,,6/12/26 07:36:08,STOCK,BUY,'+qty+',TO OPEN,SPCX,,,STOCK,'+price+',LMT,GTC,'+status+'\n\nAccount Trade History\n,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type\n\n')
    return path


def purchase():
    return ['06/15 Sale                  UNH     UNITEDHEALTH GROUP INC      (100.0000) 300.0000',
            '      Purchase              SPCX    SPACE EX TECH SPACEX CLASS     37.0000     135.0000     (4,995.00)']


def recover(path, lines, trades=()):
    return recover_statement_allocations(path, lines, trades, start=date(2026,6,1), end=date(2026,6,30))


def test_statement_and_special_order_recover_ipo_with_price(tmp_path):
    trade, = recover(order_file(tmp_path), purchase())
    execution, = trade.legs[0].executions
    assert trade.legs[0].instrument == Instrument('SPCX')
    assert execution.quantity == Decimal('37')
    assert execution.execution_price == Decimal('135')
    assert execution.executed_at == datetime(2026,6,12)
    assert execution.quantity * execution.execution_price == Decimal('4995')


@pytest.mark.parametrize('status', ['CANCELED', 'REJECTED', 'FILLED'])
def test_unconfirmed_special_order_never_creates_trade(tmp_path, status):
    assert recover(order_file(tmp_path,status=status), purchase()) == ()


def test_order_alone_cannot_create_allocation(tmp_path):
    assert recover(order_file(tmp_path), []) == ()


def test_statement_alone_or_different_amount_cannot_create_allocation(tmp_path):
    assert recover(order_file(tmp_path,qty='+38'), purchase()) == ()
    assert recover(order_file(tmp_path), [s.replace('4,995.00','4,999.00') for s in purchase()]) == ()


def test_existing_trade_or_ambiguous_statement_does_not_duplicate(tmp_path):
    path = order_file(tmp_path)
    trade, = recover(path, purchase())
    assert recover(path, purchase(), (trade,)) == ()
    assert recover(path, purchase()+purchase()) == ()


def assignment():
    return read_option_assignment_events(['06/01','Other Activity','Option Assignment','GS','06/18/2026 720.00 C','CALL GOLDMAN SACHS GROUP','1.0000'])[0]


def delivery():
    return PositionEvent(PositionEventKind.EXPIRATION,(PositionChange(Instrument('GS'),Decimal('-100')),),datetime(2026,5,30,4,5,52))


def test_verified_carried_delivery_closes_call_without_repeating_stock():
    original = assignment()
    adjusted, = carry_assignment_deliveries((original,), (delivery(),), period_start=date(2026,6,1))
    assert len(original.changes) == 2
    assert adjusted.changes == original.changes[:1]
    book = LotBook()
    book.seed(Lot('OLD-CALL', original.changes[0].instrument, Decimal('-1'), datetime(2026,4,21),None))
    history = PositionHistory();history.add_event(adjusted)
    # The replacement shares are purchased later in June.
    from campaigniq.domain.execution import Execution
    from campaigniq.domain.leg import Leg
    from campaigniq.domain.trade import Trade
    from campaigniq.domain.side import Side
    from campaigniq.domain.position_effect import PositionEffect
    history.add_trade(Trade((Leg(Instrument('GS'),Side.BUY,PositionEffect.OPEN,(Execution(Decimal('100'),Decimal('1100.37'),datetime(2026,6,23)),)),)))
    ending = LotBookPeriodApplier().apply(opening_lot_book=book,position_history=history,campaigns=())
    assert ending.lots(original.changes[0].instrument) == ()
    assert sum(l.quantity for l in ending.lots(Instrument('GS'))) == 100


def test_without_carry_evidence_assignment_remains_complete():
    event = assignment()
    assert carry_assignment_deliveries((event,), (), period_start=date(2026,6,1)) == (event,)


def test_carry_evidence_is_consumed_once():
    event = assignment()
    first, second = carry_assignment_deliveries((event,event),(delivery(),),period_start=date(2026,6,1))
    assert len(first.changes) == 1 and second == event


def test_prior_delivery_requires_approved_exact_exception(tmp_path):
    path = order_file(tmp_path)
    storage = LocalFilesystemArtifactStorage(tmp_path/'state')
    book = LotBook()
    assert _verified_prior_deliveries(path,period_start=date(2026,6,1),opening_lot_book=book,storage=storage) == ()

    def write_decision(computed, snapshot, approved=True):
        d = ReconciliationDecision(date(2026,5,1),date(2026,5,31),BOUNDARY_TIMING_EXCEPTION,ACCEPT_TRANSACTION_DERIVED_STATE,'Settlement crosses month end',('Dated broker history',),(PersistedReconciliationMismatch(Instrument('GS'),Decimal(computed),Decimal(snapshot)),),approved)
        storage.write_text(reconciliation_decision_key(period_end=d.period_end),serialize_reconciliation_decision(d))
    write_decision('0','100')
    assert _verified_prior_deliveries(path,period_start=date(2026,6,1),opening_lot_book=book,storage=storage) == (delivery(),)
    write_decision('0','99')
    assert _verified_prior_deliveries(path,period_start=date(2026,6,1),opening_lot_book=book,storage=storage) == ()


def test_full_pipeline_and_journal_preserve_ipo_and_replacement_stock(tmp_path):
    from campaigniq.import_pipeline import PeriodImportPipeline
    from campaigniq.persistence.position_journal import serialize_position_journal
    path = order_file(tmp_path)
    with path.open('a') as stream:
        stream.write(',6/23/26 11:22:30,STOCK,BUY,+100,TO OPEN,GS,,,STOCK,1100.37,1100.37,LMT\n')
        stream.write(',6/23/26 12:24:10,STOCK,BUY,+63,TO OPEN,SPCX,,,STOCK,160.485,160.485,LMT\n\nForex Statements\nTrade Date,Exec Date,Exec Time,Type,Ref #,Description,Commissions & Fees,Amount,Balance\n\n')
    book = LotBook()
    event = assignment()
    book.seed(Lot('CALL', event.changes[0].instrument, Decimal('-1'), datetime(2026,4,21),None))
    lines = ['06/01','Other Activity','Option Assignment','GS','06/18/2026 720.00 C','CALL GOLDMAN SACHS GROUP','1.0000',*purchase()]
    result = PeriodImportPipeline().run(
        period_start=date(2026,6,1),period_end=date(2026,6,30),
        thinkorswim_trade_history=path,carried_opening_lot_book=book,
        assignment_lines=(lines,),previously_applied_deliveries=(delivery(),),
    )
    assert sum(l.quantity for l in result.ending_lot_book.lots(Instrument('GS'))) == 100
    assert sum(l.quantity for l in result.ending_lot_book.lots(Instrument('SPCX'))) == 100
    assert result.ending_lot_book.lots(event.changes[0].instrument) == ()
    journal = serialize_position_journal(result,period_start=date(2026,6,1),period_end=date(2026,6,30))
    assert '135.00' in journal
