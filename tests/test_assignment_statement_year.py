"""December statement contract evidence and explicit statement-year controls."""
from datetime import date, datetime
from decimal import Decimal
import pytest

from campaigniq.importers.schwab.option_assignment_section_reader import read_option_assignment_section
from campaigniq.importers.schwab.option_assignment_flow import read_option_assignment_events
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_event_reconciler import PositionEventReconciler
from campaigniq.domain.value_objects.instrument import Instrument

HEADER = ['Account Number Statement Period', 'December 1-31, 2025']
BLOCKS = {'COST': '12/15 Purchase\n       Other         Option          COST          PUT COSTCO WHSL CORP NEW$995                             5.0000\n       Activity      Assignment      01/16/2026    EXP 01/16/26\n                                     995.00 P', 'COIN': '12/19 Purchase\n       Other         Option           COIN 01/16/2026 PUT COINBASE GLOBAL INC $360                               5.0000\n       Activity      Assignment       360.00 P        EXP 01/16/26', 'NFLX': '12/19 Purchase\n       Other         Option           NFLX            PUT NETFLIX INC            $114        EXP                50.0000\n       Activity      Assignment       01/16/2026      01/16/26\n                                      114.00 P', 'ORCL': '12/19 Purchase\n       Other         Option           ORCL            PUT ORACLE CORP              $270                          5.0000\n       Activity      Assignment       01/16/2026      EXP 01/16/26\n                                      270.00 P'}

@pytest.mark.parametrize('symbol',list(BLOCKS))
def test_literal_december_assignments_use_statement_year(symbol):
    row,=read_option_assignment_section(HEADER+BLOCKS[symbol].splitlines())
    assert row.transaction_date==date(2025,12,15 if symbol=='COST' else 19)
    assert row.expiration==date(2026,1,16)
    assert row.option_type=='PUT'
    assert row.quantity==Decimal('50' if symbol=='NFLX' else '5')
    assert row.strike==Decimal({'COST':'995','COIN':'360','NFLX':'114','ORCL':'270'}[symbol])

@pytest.mark.parametrize('symbol',list(BLOCKS))
def test_month_filter_includes_assignment_and_reconciler_keeps_delivery_once(symbol):
    events=read_option_assignment_events(HEADER+BLOCKS[symbol].splitlines())
    relevant=[e for e in events if date(2025,12,1)<=e.occurred_at.date()<=date(2025,12,31)]
    assert len(relevant)==1
    event=relevant[0]
    option,stock=event.changes
    assert -Decimal('50' if symbol=='NFLX' else '5')+option.quantity==0
    assert stock.quantity==option.quantity*100
    delivery=PositionEvent(kind=PositionEventKind.EXPIRATION,
        changes=(PositionChange(Instrument(symbol),stock.quantity),),
        occurred_at=datetime.combine(event.occurred_at.date(),datetime.min.time()))
    reconciled=PositionEventReconciler().reconcile((delivery,event))
    assert reconciled==(event,)


def legacy_lines(expiration='01/16/2026',day='12/19'):
    return [day,'Other Activity','Option Assignment','COIN',
            expiration+' 360.00 P','PUT COINBASE GLOBAL INC','5.0000']


def test_legacy_vertical_layout_uses_explicit_statement_year():
    row,=read_option_assignment_section(HEADER+legacy_lines())
    assert row.transaction_date==date(2025,12,19)
    assert row.expiration==date(2026,1,16)

@pytest.mark.parametrize('legacy',[True,False])
def test_january_posting_of_december_contract_uses_statement_year(legacy):
    if legacy:
        body=legacy_lines('12/31/2025','01/02')
    else:
        body=BLOCKS['COIN'].replace('12/19 Purchase','01/02 Purchase').replace('01/16/2026','12/31/2025').replace('01/16/26','12/31/25').splitlines()
    row,=read_option_assignment_section(['Statement Period January 1-31, 2026']+body)
    assert row.transaction_date==date(2026,1,2)
    assert row.expiration==date(2025,12,31)


def test_long_dated_early_assignment_does_not_use_expiry_year():
    row,=read_option_assignment_section(HEADER+legacy_lines('01/15/2027'))
    assert row.transaction_date==date(2025,12,19)
    assert row.expiration.year==2027


def test_repeated_page_headers_are_consistent():
    rows=read_option_assignment_section(HEADER+BLOCKS['COIN'].splitlines()+HEADER+BLOCKS['ORCL'].splitlines())
    assert len(rows)==2
    assert all(r.transaction_date.year==2025 for r in rows)

@pytest.mark.parametrize('header',[
    ['Statement Period','unreadable'],
    ['Statement Period unreadable'],
    HEADER+['Statement Period January 1-31, 2026'],
    ['Statement Period December 31-1, 2025'],
])
def test_unreadable_conflicting_or_reversed_period_fails_closed(header):
    with pytest.raises(ValueError,match='statement period'):
        read_option_assignment_section(header+BLOCKS['COIN'].splitlines())

@pytest.mark.parametrize('legacy',[True,False])
def test_transaction_outside_statement_period_is_not_reassigned(legacy):
    body=legacy_lines(day='11/19') if legacy else BLOCKS['COIN'].replace('12/19 Purchase','11/19 Purchase').splitlines()
    with pytest.raises(ValueError,match='outside statement period'):
        read_option_assignment_section(HEADER+body)


def test_explicit_trade_date_kept_separate():
    body=legacy_lines()
    body.insert(1,'Trade Date: 12/18/25')
    row,=read_option_assignment_section(HEADER+body)
    assert row.transaction_date==date(2025,12,19)
    assert row.trade_date==date(2025,12,18)


def test_headerless_legacy_fragments_keep_existing_convention():
    row,=read_option_assignment_section(legacy_lines('12/19/2025'))
    assert row.transaction_date==date(2025,12,19)


def test_no_assignment_is_inferred_from_header_or_stock_purchase():
    assert read_option_assignment_section(HEADER+['12/19 Purchase COIN 500.0000'])==[]


def test_terms_and_conditions_are_not_statement_headers():
    row,=read_option_assignment_section(HEADER+BLOCKS['COIN'].splitlines()+[
        'Interest may be paid for a period that differs from the Statement Period after interest is posted.'])
    assert row.transaction_date==date(2025,12,19)


def test_leap_day_comes_from_statement_year():
    row,=read_option_assignment_section(['Statement Period February 1-29, 2024']+legacy_lines('01/17/2025','02/29'))
    assert row.transaction_date==date(2024,2,29)


def test_nearest_invalid_date_is_not_skipped_for_an_earlier_one():
    with pytest.raises(ValueError,match='outside statement period'):
        read_option_assignment_section(HEADER+['12/18']+legacy_lines(day='12/32'))
