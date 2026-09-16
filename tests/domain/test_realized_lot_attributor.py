from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.leg import Leg
from campaigniq.domain.value_objects.instrument import Instrument


def test_coins_january_sale_uses_december_lot_and_schwab_basis() -> None:
    book = LotBook()
    book.seed(
        Lot(
            lot_id="DEC-COIN-500",
            instrument=Instrument("COIN"),
            quantity=Decimal("500"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("128703.31"),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )

    trade = Trade(
        legs=(InstrumentLeg(
            instrument=Instrument("COIN"),
            side=Side.SELL,
            position_effect=PositionEffect.CLOSE,
            executions=(Execution(
                quantity=Decimal("500"),
                execution_price=Decimal("217.44"),
                executed_at=datetime(2026, 1, 23, 13, 12, 35),
            ),),
        ),),
    )
    record = RealizedGainLossRecord(
        closed_date=date(2026, 1, 23),
        instrument=Instrument("COIN"),
        quantity=Decimal("500"),
        closing_price=Decimal("217.44"),
        proceeds=Decimal("108719.90"),
        cost_basis=Decimal("128703.31"),
        gain_loss=Decimal("-19983.41"),
        basis_method="FIFO",
        term="SHORT TERM",
    )

    result = RealizedLotAttributor(book).attribute([trade], [record])[0]
    assert result.allocations[0].lot_id == "DEC-COIN-500"
    assert result.basis_reconciled is True
    assert result.gain_loss_reconciled is True


def test_broker_record_can_aggregate_multiple_closing_fills() -> None:
    book = LotBook()
    book.seed(
        Lot(
            lot_id="PRE-NVDA-5",
            instrument=Instrument("NVDA-184P"),
            quantity=Decimal("-5"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=None,
            basis_source="DECEMBER_SNAPSHOT",
        )
    )

    # Use a generic instrument to exercise aggregation independently of the
    # option-contract parser. Schwab reports one 5-unit realized record while
    # the broker trade history can contain 4 + 1 closing fills.
    trades = [
        Trade(legs=(InstrumentLeg(
            instrument=Instrument("NVDA-184P"),
            side=Side.BUY,
            position_effect=PositionEffect.CLOSE,
            executions=(Execution(
                quantity=Decimal("4"),
                execution_price=Decimal("0.22"),
                executed_at=datetime(2026, 1, 16, 12, 1, 31),
            ),),
        ),)),
        Trade(legs=(InstrumentLeg(
            instrument=Instrument("NVDA-184P"),
            side=Side.BUY,
            position_effect=PositionEffect.CLOSE,
            executions=(Execution(
                quantity=Decimal("1"),
                execution_price=Decimal("0.22"),
                executed_at=datetime(2026, 1, 16, 12, 1, 31),
            ),),
        ),)),
    ]
    record = RealizedGainLossRecord(
        closed_date=date(2026, 1, 16),
        instrument=Instrument("NVDA-184P"),
        quantity=Decimal("5"),
        closing_price=Decimal("10.78"),
        proceeds=Decimal("5391.69"),
        cost_basis=Decimal("113.30"),
        gain_loss=Decimal("5278.39"),
        basis_method="FIFO",
        term="SHORT TERM",
    )

    result = RealizedLotAttributor(book).attribute(trades, [record])[0]
    assert result.allocated_quantity == Decimal("5")
    assert [a.quantity for a in result.allocations] == [Decimal("4"), Decimal("1")]
    assert result.basis_reconciled is True
    assert result.gain_loss_reconciled is True


def test_assignment_settlement_uses_assignment_date_for_realized_record() -> None:
    from campaigniq.domain.option_contract import OptionContract
    from campaigniq.domain.option_type import OptionType
    from campaigniq.domain.position_event import PositionChange, PositionEvent
    from campaigniq.domain.position_event_kind import PositionEventKind

    book = LotBook()
    book.seed(
        Lot(
            lot_id="DEC-DXCM-500",
            instrument=Instrument("DXCM"),
            quantity=Decimal("500"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("34835.00"),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )
    event = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(PositionChange(
            instrument=OptionContract(
                underlying="DXCM",
                expiration=date(2026, 1, 9),
                strike=Decimal("67"),
                option_type=OptionType.CALL,
            ),
            quantity=Decimal("5"),
        ),),
        occurred_at=datetime(2026, 1, 9, 16, 0),
    )
    trade = Trade(
        legs=(InstrumentLeg(
            instrument=Instrument("DXCM"),
            side=Side.SELL,
            position_effect=PositionEffect.CLOSE,
            executions=(Execution(
                quantity=Decimal("500"),
                execution_price=Decimal("67.00"),
                executed_at=datetime(2026, 1, 12, 10, 0),
            ),),
        ),),
    )
    record = RealizedGainLossRecord(
        closed_date=date(2026, 1, 9),
        instrument=Instrument("DXCM"),
        quantity=Decimal("500"),
        closing_price=Decimal("69.96"),
        proceeds=Decimal("34981.59"),
        cost_basis=Decimal("34835.00"),
        gain_loss=Decimal("146.59"),
        basis_method="FIFO",
        term="SHORT TERM",
    )

    result = RealizedLotAttributor(book).attribute([trade], [record], [event])[0]

    assert result.allocations[0].lot_id == "DEC-DXCM-500"
    assert result.allocated_quantity == Decimal("500")
    assert result.basis_reconciled is True
    assert result.gain_loss_reconciled is True

def test_assignment_settlement_can_precede_assignment_event() -> None:
    from campaigniq.domain.option_contract import OptionContract
    from campaigniq.domain.option_type import OptionType
    from campaigniq.domain.position_event import PositionChange, PositionEvent
    from campaigniq.domain.position_event_kind import PositionEventKind

    assignment = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(PositionChange(
            instrument=OptionContract(
                underlying="UNH",
                expiration=date(2026, 3, 20),
                strike=Decimal("250"),
                option_type=OptionType.CALL,
            ),
            quantity=Decimal("5"),
        ),),
        occurred_at=datetime(2026, 3, 9),
    )

    settlement = InstrumentLeg(
        instrument=Instrument("UNH"),
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE,
        executions=(Execution(
            quantity=Decimal("500"),
            execution_price=Decimal("250"),
            executed_at=datetime(2026, 3, 7),
        ),),
    )

    closures = RealizedLotAttributor._assignment_closures([assignment])

    assert RealizedLotAttributor._economic_closed_date(
        settlement, closures
    ) == date(2026, 3, 7)
    assert closures[0].remaining_quantity == Decimal("500")

def test_assignment_dates_ignore_non_assignment_events() -> None:
    from campaigniq.domain.option_contract import OptionContract
    from campaigniq.domain.option_type import OptionType
    from campaigniq.domain.position_event import PositionChange, PositionEvent
    from campaigniq.domain.position_event_kind import PositionEventKind

    instrument = OptionContract(
        underlying="DXCM",
        expiration=date(2026, 1, 9),
        strike=Decimal("67"),
        option_type=OptionType.CALL,
    )
    events = [
        PositionEvent(
            kind=PositionEventKind.EXPIRATION,
            changes=(PositionChange(instrument=instrument, quantity=Decimal("5")),),
            occurred_at=datetime(2026, 1, 9, 16, 0),
        ),
        PositionEvent(
            kind=PositionEventKind.ASSIGNMENT,
            changes=(PositionChange(instrument=instrument, quantity=Decimal("5")),),
            occurred_at=datetime(2026, 1, 12, 16, 0),
        ),
    ]

    result = RealizedLotAttributor._assignment_dates(events)

    assert result == {Instrument("DXCM"): [date(2026, 1, 12)]}


def test_assignment_settlement_requires_matching_strike() -> None:
    from campaigniq.domain.option_contract import OptionContract
    from campaigniq.domain.option_type import OptionType
    from campaigniq.domain.position_event import PositionChange, PositionEvent
    from campaigniq.domain.position_event_kind import PositionEventKind

    assignment = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(PositionChange(
            instrument=OptionContract(
                underlying="DXCM",
                expiration=date(2026, 1, 9),
                strike=Decimal("67"),
                option_type=OptionType.CALL,
            ),
            quantity=Decimal("5"),
        ),),
        occurred_at=datetime(2026, 1, 9, 16, 0),
    )
    leg = InstrumentLeg(
        instrument=Instrument("DXCM"),
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE,
        executions=(Execution(
            quantity=Decimal("500"),
            execution_price=Decimal("70"),
            executed_at=datetime(2026, 1, 12, 10, 0),
        ),),
    )

    closures = RealizedLotAttributor._assignment_closures([assignment])

    assert RealizedLotAttributor._economic_closed_date(leg, closures) == date(2026, 1, 12)


def test_assignment_settlement_consumes_matching_quantity() -> None:
    from campaigniq.domain.option_contract import OptionContract
    from campaigniq.domain.option_type import OptionType
    from campaigniq.domain.position_event import PositionChange, PositionEvent
    from campaigniq.domain.position_event_kind import PositionEventKind

    assignment = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(PositionChange(
            instrument=OptionContract(
                underlying="DXCM",
                expiration=date(2026, 1, 9),
                strike=Decimal("67"),
                option_type=OptionType.CALL,
            ),
            quantity=Decimal("5"),
        ),),
        occurred_at=datetime(2026, 1, 9, 16, 0),
    )
    closures = RealizedLotAttributor._assignment_closures([assignment])

    def close(qty: str, when: int) -> Leg:
        return InstrumentLeg(
            instrument=Instrument("DXCM"),
            side=Side.SELL,
            position_effect=PositionEffect.CLOSE,
            executions=(Execution(
                quantity=Decimal(qty),
                execution_price=Decimal("67"),
                executed_at=datetime(2026, 1, when, 10, 0),
            ),),
        )

    assert RealizedLotAttributor._economic_closed_date(close("300", 12), closures) == date(2026, 1, 9)
    assert RealizedLotAttributor._economic_closed_date(close("200", 12), closures) == date(2026, 1, 9)
    assert RealizedLotAttributor._economic_closed_date(close("100", 13), closures) == date(2026, 1, 13)


def test_call_assignment_event_can_close_stock_without_settlement_trade() -> None:
    from campaigniq.domain.option_contract import OptionContract
    from campaigniq.domain.option_type import OptionType
    from campaigniq.domain.position_event import PositionChange, PositionEvent
    from campaigniq.domain.position_event_kind import PositionEventKind

    book = LotBook()
    book.seed(
        Lot(
            lot_id="DEC-APD-100",
            instrument=Instrument("APD"),
            quantity=Decimal("100"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("25000"),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )

    event = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=OptionContract(
                    underlying="APD",
                    expiration=date(2026, 7, 17),
                    strike=Decimal("270"),
                    option_type=OptionType.CALL,
                ),
                quantity=Decimal("1"),
            ),
            PositionChange(
                instrument=Instrument("APD"),
                quantity=Decimal("-100"),
            ),
        ),
        occurred_at=datetime(2026, 7, 20),
    )
    record = RealizedGainLossRecord(
        closed_date=date(2026, 7, 20),
        instrument=Instrument("APD"),
        quantity=Decimal("100"),
        closing_price=Decimal("270"),
        proceeds=Decimal("27000"),
        cost_basis=Decimal("25000"),
        gain_loss=Decimal("2000"),
        basis_method="FIFO",
        term="SHORT TERM",
    )

    result = RealizedLotAttributor(book).attribute([], [record], [event])[0]

    assert result.allocations[0].lot_id == "DEC-APD-100"
    assert result.allocated_quantity == Decimal("100")
    assert result.basis_reconciled is True
    assert result.gain_loss_reconciled is True


def test_assignment_settlement_does_not_match_a_later_independent_sale() -> None:
    from campaigniq.domain.option_contract import OptionContract
    from campaigniq.domain.option_type import OptionType
    from campaigniq.domain.position_event import PositionChange, PositionEvent
    from campaigniq.domain.position_event_kind import PositionEventKind

    assignment = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=OptionContract(
                    underlying="DXCM",
                    expiration=date(2026, 1, 9),
                    strike=Decimal("67"),
                    option_type=OptionType.CALL,
                ),
                quantity=Decimal("5"),
            ),
            PositionChange(
                instrument=Instrument("DXCM"),
                quantity=Decimal("-500"),
            ),
        ),
        occurred_at=datetime(2026, 1, 9, 16, 0),
    )
    closures = RealizedLotAttributor._assignment_closures([assignment])

    later_sale = InstrumentLeg(
        instrument=Instrument("DXCM"),
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE,
        executions=(Execution(
            quantity=Decimal("500"),
            execution_price=Decimal("67"),
            executed_at=datetime(2026, 1, 23, 10, 0),
        ),),
    )

    assert RealizedLotAttributor._economic_closed_date(
        later_sale, closures
    ) == date(2026, 1, 23)
    assert closures[0].remaining_quantity == Decimal("500")


def test_assignment_stock_change_is_reduced_by_matched_settlement_quantity() -> None:
    from campaigniq.domain.option_contract import OptionContract
    from campaigniq.domain.option_type import OptionType
    from campaigniq.domain.position_event import PositionChange, PositionEvent
    from campaigniq.domain.position_event_kind import PositionEventKind

    book = LotBook()
    book.seed(
        Lot(
            lot_id="DEC-DXCM-500",
            instrument=Instrument("DXCM"),
            quantity=Decimal("500"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("34835.00"),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )
    assignment = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=OptionContract(
                    underlying="DXCM",
                    expiration=date(2026, 1, 9),
                    strike=Decimal("67"),
                    option_type=OptionType.CALL,
                ),
                quantity=Decimal("5"),
            ),
            PositionChange(
                instrument=Instrument("DXCM"),
                quantity=Decimal("-500"),
            ),
        ),
        occurred_at=datetime(2026, 1, 9, 16, 0),
    )
    settlement = InstrumentLeg(
        instrument=Instrument("DXCM"),
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE,
        executions=(Execution(
            quantity=Decimal("300"),
            execution_price=Decimal("67"),
            executed_at=datetime(2026, 1, 12, 10, 0),
        ),),
    )

    RealizedLotAttributor(book).attribute(
        [Trade(legs=(settlement,))],
        [],
        [assignment],
    )

    assert sum(lot.quantity for lot in book.lots(Instrument("DXCM"))) == Decimal("0")


def test_assignment_settlement_can_precede_event_when_event_confirms_stock_change() -> None:
    from campaigniq.domain.option_contract import OptionContract
    from campaigniq.domain.option_type import OptionType
    from campaigniq.domain.position_event import PositionChange, PositionEvent
    from campaigniq.domain.position_event_kind import PositionEventKind

    assignment = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=OptionContract(
                    underlying="GS",
                    expiration=date(2026, 6, 18),
                    strike=Decimal("720"),
                    option_type=OptionType.CALL,
                ),
                quantity=Decimal("1"),
            ),
            PositionChange(
                instrument=Instrument("GS"),
                quantity=Decimal("-100"),
            ),
        ),
        occurred_at=datetime(2026, 6, 1),
    )

    settlement = InstrumentLeg(
        instrument=Instrument("GS"),
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE,
        executions=(
            Execution(
                quantity=Decimal("100"),
                execution_price=Decimal("720"),
                executed_at=datetime(2026, 5, 30),
            ),
        ),
    )

    closures = RealizedLotAttributor._assignment_closures([assignment])

    assert RealizedLotAttributor._matches_assignment_closure(
        settlement,
        closures,
    ) is True

    assert RealizedLotAttributor._economic_closed_date(
        settlement,
        closures,
    ) == date(2026, 6, 1)

    assert closures[0].remaining_quantity == Decimal("0")


def test_preceding_gs_assignment_settlement_closes_stock_exactly_once() -> None:
    from campaigniq.domain.option_contract import OptionContract
    from campaigniq.domain.option_type import OptionType
    from campaigniq.domain.position_event import PositionChange, PositionEvent
    from campaigniq.domain.position_event_kind import PositionEventKind

    book = LotBook()
    book.seed(
        Lot(
            lot_id="APRIL-GS-100",
            instrument=Instrument("GS"),
            quantity=Decimal("100"),
            opened_at=datetime(2026, 4, 30, 16, 0),
            basis_total=Decimal("92053.00"),
            basis_source="APRIL_SNAPSHOT",
        )
    )

    assignment = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=OptionContract(
                    underlying="GS",
                    expiration=date(2026, 6, 18),
                    strike=Decimal("720"),
                    option_type=OptionType.CALL,
                ),
                quantity=Decimal("1"),
            ),
            PositionChange(
                instrument=Instrument("GS"),
                quantity=Decimal("-100"),
            ),
        ),
        occurred_at=datetime(2026, 6, 1),
    )

    settlement = InstrumentLeg(
        instrument=Instrument("GS"),
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE,
        executions=(
            Execution(
                quantity=Decimal("100"),
                execution_price=Decimal("720"),
                executed_at=datetime(2026, 5, 30),
            ),
        ),
    )

    RealizedLotAttributor(book).attribute(
        [Trade(legs=(settlement,))],
        [],
        [assignment],
    )

    assert (
        sum(
            lot.quantity
            for lot in book.lots(Instrument("GS"))
        )
        == Decimal("0")
    )

def test_assignment_like_saturday_stock_event_matches_prior_friday_realized_record() -> None:
    from datetime import date, datetime
    from decimal import Decimal

    from campaigniq.domain.lot import Lot
    from campaigniq.domain.lot_book import LotBook
    from campaigniq.domain.position_event import PositionChange, PositionEvent
    from campaigniq.domain.position_event_kind import PositionEventKind
    from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
    from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
    from campaigniq.domain.value_objects.instrument import Instrument

    book = LotBook()
    book.seed(
        Lot(
            lot_id="OPEN-GS-100",
            instrument=Instrument("GS"),
            quantity=Decimal("100"),
            opened_at=datetime(2026, 2, 20, 13, 14, 25),
            basis_total=None,
            basis_source=None,
            campaign_id="CAMP-GS",
        )
    )

    event = PositionEvent(
        kind=PositionEventKind.EXPIRATION,
        changes=(
            PositionChange(
                instrument=Instrument("GS"),
                quantity=Decimal("-100"),
            ),
        ),
        occurred_at=datetime(2026, 5, 30, 4, 5, 52),
    )

    record = RealizedGainLossRecord(
        closed_date=date(2026, 5, 29),
        instrument=Instrument("GS"),
        quantity=Decimal("100"),
        closing_price=Decimal("936.19"),
        proceeds=Decimal("93619.39"),
        cost_basis=Decimal("92053.00"),
        gain_loss=Decimal("1566.39"),
        basis_method="FIFO",
        term="SHORT",
    )

    attribution = RealizedLotAttributor(book).attribute(
        [],
        [record],
        [event],
    )[0]

    assert attribution.allocated_quantity == Decimal("100")
    assert attribution.allocations[0].lot_id == "OPEN-GS-100"
    assert attribution.allocations[0].campaign_id == "CAMP-GS"


def test_next_day_assignment_event_matches_prior_day_broker_realized_record() -> None:
    from datetime import date, datetime
    from decimal import Decimal

    from campaigniq.domain.lot import Lot
    from campaigniq.domain.lot_book import LotBook
    from campaigniq.domain.option_contract import OptionContract
    from campaigniq.domain.option_type import OptionType
    from campaigniq.domain.position_event import PositionChange, PositionEvent
    from campaigniq.domain.position_event_kind import PositionEventKind
    from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
    from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
    from campaigniq.domain.value_objects.instrument import Instrument

    book = LotBook()
    book.seed(
        Lot(
            lot_id="OPEN-APD-100",
            instrument=Instrument("APD"),
            quantity=Decimal("100"),
            opened_at=datetime(2026, 4, 17, 10, 46, 47),
            basis_total=None,
            basis_source=None,
            campaign_id="CAMP-APD",
        )
    )
    book.seed(
        Lot(
            lot_id="OPEN-APD-CALL",
            instrument=OptionContract(
                underlying="APD",
                expiration=date(2026, 7, 17),
                strike=Decimal("270.00"),
                option_type=OptionType.CALL,
            ),
            quantity=Decimal("-1"),
            opened_at=datetime(2026, 6, 18, 9, 34, 45),
            basis_total=None,
            basis_source=None,
            campaign_id="CAMP-APD",
        )
    )

    event = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=OptionContract(
                    underlying="APD",
                    expiration=date(2026, 7, 17),
                    strike=Decimal("270.00"),
                    option_type=OptionType.CALL,
                ),
                quantity=Decimal("1"),
            ),
            PositionChange(
                instrument=Instrument("APD"),
                quantity=Decimal("-100"),
            ),
        ),
        occurred_at=datetime(2026, 7, 1),
    )

    record = RealizedGainLossRecord(
        closed_date=date(2026, 6, 30),
        instrument=Instrument("APD"),
        quantity=Decimal("100"),
        closing_price=Decimal("283.74"),
        proceeds=Decimal("28373.73"),
        cost_basis=Decimal("29349.00"),
        gain_loss=Decimal("-975.27"),
        basis_method="FIFO",
        term="SHORT",
    )

    attribution = RealizedLotAttributor(book).attribute(
        [],
        [record],
        [event],
    )[0]

    assert attribution.allocated_quantity == Decimal("100")
    assert attribution.allocations[0].lot_id == "OPEN-APD-100"
    assert attribution.allocations[0].campaign_id == "CAMP-APD"
    assert attribution.basis_reconciled is True
    assert attribution.gain_loss_reconciled is True


def test_campaign_close_assigns_only_consumed_unassigned_equity_to_campaign() -> None:
    """A campaign close supplies provenance for the exact stock it consumes."""
    book = LotBook()
    book.seed(
        Lot(
            lot_id="DEC-HD-500",
            instrument=Instrument("HD"),
            quantity=Decimal("500"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("175299.07"),
            basis_source="DECEMBER_SNAPSHOT",
            campaign_id=None,
        )
    )

    trade = Trade(
        legs=(
            InstrumentLeg(
                instrument=Instrument("HD"),
                side=Side.SELL,
                position_effect=PositionEffect.CLOSE,
                executions=(
                    Execution(
                        quantity=Decimal("-400"),
                        execution_price=Decimal("324.23"),
                        executed_at=datetime(2026, 5, 7, 8, 5, 52),
                    ),
                ),
            ),
        ),
    )

    record = RealizedGainLossRecord(
        closed_date=date(2026, 5, 7),
        instrument=Instrument("HD"),
        quantity=Decimal("400"),
        closing_price=Decimal("324.22"),
        proceeds=Decimal("129689.25"),
        cost_basis=Decimal("140239.26"),
        gain_loss=Decimal("-10550.01"),
        basis_method="FIFO",
        term="SHORT",
    )

    class CampaignStub:
        campaign_id = "CAMP-HD"

        def __init__(self, trades):
            self.trades = trades

    result = RealizedLotAttributor(book).attribute_campaigns(
        [CampaignStub([trade])],
        [record],
    )[0]

    assert result.allocated_quantity == Decimal("400")
    assert result.allocations[0].lot_id == "DEC-HD-500"
    assert result.allocations[0].campaign_id == "CAMP-HD"
    assert result.has_unassigned_campaign_allocation is False
    assert result.basis_reconciled is True
    assert result.gain_loss_reconciled is True

    remaining = book.lots(Instrument("HD"))

    assert len(remaining) == 1
    assert remaining[0].quantity == Decimal("100")
    assert remaining[0].campaign_id is None
