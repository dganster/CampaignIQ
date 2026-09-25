from datetime import datetime
from decimal import Decimal

D = Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_exit import detect_position_exit
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument


NOW = datetime(2026, 4, 16, 10, 52, 1)


def call(strike: str = "74") -> OptionContract:
    from datetime import date

    return OptionContract(
        underlying="NFLX",
        expiration=date(2026, 5, 15),
        strike=D(strike),
        option_type=OptionType.CALL,
    )


def seed(
    book: LotBook,
    instrument,
    quantity: str,
) -> None:
    book.seed(
        Lot(
            lot_id=f"OPEN-{len(book.lots(instrument))}",
            instrument=instrument,
            quantity=D(quantity),
            opened_at=datetime(2026, 3, 27),
            basis_total=D("0"),
            basis_source="TEST",
        )
    )


def stock_close(quantity: str) -> InstrumentLeg:
    return InstrumentLeg(
        instrument=Instrument("NFLX"),
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE,
        executions=(Execution(D(quantity), D("107.24"), NOW),),
    )


def call_close(quantity: str) -> OptionLeg:
    return OptionLeg(
        contract=call(),
        side=Side.BUY,
        position_effect=PositionEffect.CLOSE,
        executions=(Execution(D(quantity), D("33.61"), NOW),),
        broker_strategy="COVERED",
    )


def test_complete_stock_and_short_call_close_is_exit() -> None:
    book = LotBook()
    seed(book, Instrument("NFLX"), "5000")
    seed(book, call(), "-50")

    trade = Trade(
        legs=(
            call_close("50"),
            stock_close("-5000"),
        )
    )

    exits = detect_position_exit(
        opening_lot_book=book,
        trade=trade,
    )

    assert len(exits) == 1
    assert exits[0].underlying == "NFLX"
    assert len(exits[0].before_positions) == 2
    assert exits[0].after_positions == ()


def test_partial_stock_close_is_not_exit() -> None:
    book = LotBook()
    seed(book, Instrument("NFLX"), "5000")

    trade = Trade(legs=(stock_close("-2500"),))

    assert detect_position_exit(
        opening_lot_book=book,
        trade=trade,
    ) == ()


def test_closing_call_but_retaining_stock_is_not_exit() -> None:
    book = LotBook()
    seed(book, Instrument("NFLX"), "5000")
    seed(book, call(), "-50")

    trade = Trade(legs=(call_close("50"),))

    assert detect_position_exit(
        opening_lot_book=book,
        trade=trade,
    ) == ()


def test_closing_stock_but_retaining_call_is_not_exit() -> None:
    book = LotBook()
    seed(book, Instrument("NFLX"), "5000")
    seed(book, call(), "-50")

    trade = Trade(legs=(stock_close("-5000"),))

    assert detect_position_exit(
        opening_lot_book=book,
        trade=trade,
    ) == ()


def test_no_known_opening_position_is_not_exit() -> None:
    book = LotBook()

    trade = Trade(legs=(stock_close("-5000"),))

    assert detect_position_exit(
        opening_lot_book=book,
        trade=trade,
    ) == ()


def test_original_opening_book_is_not_mutated() -> None:
    book = LotBook()
    seed(book, Instrument("NFLX"), "5000")
    seed(book, call(), "-50")

    trade = Trade(
        legs=(
            call_close("50"),
            stock_close("-5000"),
        )
    )

    detect_position_exit(
        opening_lot_book=book,
        trade=trade,
    )

    assert sum(
        (lot.quantity for lot in book.lots(Instrument("NFLX"))),
        D("0"),
    ) == D("5000")

    assert sum(
        (lot.quantity for lot in book.lots(call())),
        D("0"),
    ) == D("-50")


def test_lot_book_instruments_reports_all_open_instruments() -> None:
    book = LotBook()
    stock = Instrument("NFLX")
    option = call()

    seed(book, stock, "5000")
    seed(book, option, "-50")

    assert set(book.instruments()) == {stock, option}


def test_lot_book_instruments_excludes_fully_closed_instrument() -> None:
    book = LotBook()
    stock = Instrument("NFLX")

    seed(book, stock, "5000")
    book.apply_trade(
        Trade(
            legs=(
                stock_close("-5000"),
            )
        )
    )

    assert stock not in book.instruments()


def test_multiple_close_legs_cannot_overconsume_opening_position() -> None:
    stock = Instrument("NFLX")

    book = LotBook()
    book.seed(
        Lot(
            lot_id="NFLX-STOCK",
            instrument=stock,
            quantity=Decimal("5000"),
            opened_at=datetime(2026, 1, 1),
            basis_total=None,
            basis_source="TEST",
        )
    )

    trade = Trade(
        legs=(
            InstrumentLeg(
                instrument=stock,
                side=Side.SELL,
                position_effect=PositionEffect.CLOSE,
                executions=(
                    Execution(
                        quantity=Decimal("-3000"),
                        execution_price=Decimal("107"),
                        executed_at=datetime(2026, 4, 16, 10, 52, 1),
                    ),
                ),
            ),
            InstrumentLeg(
                instrument=stock,
                side=Side.SELL,
                position_effect=PositionEffect.CLOSE,
                executions=(
                    Execution(
                        quantity=Decimal("-3000"),
                        execution_price=Decimal("107"),
                        executed_at=datetime(2026, 4, 16, 10, 52, 1),
                    ),
                ),
            ),
        )
    )

    assert detect_position_exit(
        opening_lot_book=book,
        trade=trade,
    ) == ()

    assert sum(
        (lot.quantity for lot in book.lots(stock)),
        Decimal("0"),
    ) == Decimal("5000")
