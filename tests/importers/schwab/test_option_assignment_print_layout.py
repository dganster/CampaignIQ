from datetime import date, datetime
from decimal import Decimal
import pytest
from campaigniq.importers.schwab.option_assignment_section_reader import read_option_assignment_section
from campaigniq.importers.schwab.option_assignment_flow import read_option_assignment_events
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_event_reconciler import PositionEventReconciler
from campaigniq.domain.value_objects.instrument import Instrument


def statement_lines():
    return [
        '01/12 Sale                           EL                LAUDER ESTEE COS INC CLASS                           (500.0000)      106.0000',
        '       Other         Option          DXCM             CALL DEXCOM INC              $67        EXP               5.0000',
        '       Activity      Assignment      01/09/2026 67.00 01/09/26',
        '                                     C',
        '       Other         Option          EL 01/09/2026     CALL LAUDER ESTEE COS IN$106                             5.0000',
        '       Activity      Assignment      106.00 C          EXP 01/09/26',
        '01/13 Sale           Short Sale      NFLX             CALL NETFLIX INC           $82        EXP              (50.0000)',
    ]


def test_reads_both_wrapped_contract_formats():
    rows = read_option_assignment_section(statement_lines())
    assert [(r.symbol, r.strike, r.quantity, r.option_type) for r in rows] == [
        ('DXCM', Decimal('67'), Decimal('5'), 'CALL'),
        ('EL', Decimal('106'), Decimal('5'), 'CALL'),
    ]
    assert all(r.expiration == date(2026, 1, 9) for r in rows)
    assert all(r.transaction_date == date(2026, 1, 12) for r in rows)


def test_assignment_closes_calls_and_delivers_stock_once():
    assignments = tuple(read_option_assignment_events(statement_lines()))
    deliveries = tuple(PositionEvent(
        kind=PositionEventKind.EXPIRATION,
        changes=(PositionChange(Instrument(symbol), Decimal('-500')),),
        occurred_at=datetime(2026, 1, 10, 1, 15, 58),
    ) for symbol in ('DXCM', 'EL'))
    events = PositionEventReconciler().reconcile((*deliveries, *assignments))
    assert events == assignments
    for event in events:
        option, stock = event.changes
        assert Decimal('-5') + option.quantity == 0
        assert Decimal('500') + stock.quantity == 0


def test_stock_sale_alone_is_not_assignment_evidence():
    assert read_option_assignment_section(statement_lines()[:1]) == []


def test_put_assignment_preserves_type_and_signed_delivery():
    lines = statement_lines()[0:4]
    lines[1] = lines[1].replace('CALL', 'PUT ')
    lines[3] = lines[3].replace('C', 'P')
    event, = read_option_assignment_events(lines)
    assert event.changes[0].quantity == Decimal('5')
    assert event.changes[1].quantity == Decimal('500')


def test_malformed_explicit_assignment_is_not_silently_ignored():
    lines = statement_lines()[:4]
    lines[2] = lines[2].replace('67.00', 'bad')
    with pytest.raises(ValueError, match='wrapped'):
        read_option_assignment_section(lines)
