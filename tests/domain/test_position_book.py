from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_book import PositionBook
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument


def stock_trade(
    *,
    side: Side,
    position_effect: PositionEffect,
    quantity: Decimal,
) -> Trade:
    leg = Leg(
        instrument=Instrument("COIN"),
        side=side,
        position_effect=position_effect,
        executions=(
            Execution(
                quantity=quantity,
                execution_price=Decimal("200"),
                executed_at=datetime(2026, 1, 5, 10, 30),
            ),
        ),
    )

    return Trade(legs=(leg,))


def option_trade(
    *,
    side: Side,
    position_effect: PositionEffect,
    quantity: Decimal,
) -> Trade:
    leg = Leg(
        instrument=OptionContract(
            underlying="COIN",
            expiration=date(2026, 2, 20),
            strike=Decimal("200"),
            option_type=OptionType.CALL,
        ),
        side=side,
        position_effect=position_effect,
        executions=(
            Execution(
                quantity=quantity,
                execution_price=Decimal("20"),
                executed_at=datetime(2026, 1, 5, 10, 30),
            ),
        ),
    )

    return Trade(legs=(leg,))


def test_opening_long_stock_position() -> None:
    book = PositionBook()

    book.apply(
        stock_trade(
            side=Side.BUY,
            position_effect=PositionEffect.OPEN,
            quantity=Decimal("500"),
        )
    )

    assert book.quantity(Instrument("COIN")) == Decimal("500")


def test_opening_short_option_position() -> None:
    book = PositionBook()

    contract = OptionContract(
        underlying="COIN",
        expiration=date(2026, 2, 20),
        strike=Decimal("200"),
        option_type=OptionType.CALL,
    )

    book.apply(
        option_trade(
            side=Side.SELL,
            position_effect=PositionEffect.OPEN,
            quantity=Decimal("-5"),
        )
    )

    assert book.quantity(contract) == Decimal("-5")


def test_closing_position_returns_to_zero() -> None:
    book = PositionBook()

    book.apply(
        stock_trade(
            side=Side.BUY,
            position_effect=PositionEffect.OPEN,
            quantity=Decimal("500"),
        )
    )

    book.apply(
        stock_trade(
            side=Side.SELL,
            position_effect=PositionEffect.CLOSE,
            quantity=Decimal("-500"),
        )
    )

    assert book.quantity(Instrument("COIN")) == Decimal("0")


def test_stock_and_option_positions_are_separate() -> None:
    book = PositionBook()

    contract = OptionContract(
        underlying="COIN",
        expiration=date(2026, 2, 20),
        strike=Decimal("200"),
        option_type=OptionType.CALL,
    )

    book.apply(
        stock_trade(
            side=Side.BUY,
            position_effect=PositionEffect.OPEN,
            quantity=Decimal("500"),
        )
    )

    book.apply(
        option_trade(
            side=Side.SELL,
            position_effect=PositionEffect.OPEN,
            quantity=Decimal("-5"),
        )
    )

    assert book.quantity(Instrument("COIN")) == Decimal("500")
    assert book.quantity(contract) == Decimal("-5")


def test_closing_trade_can_start_before_available_data() -> None:
    book = PositionBook()

    contract = OptionContract(
        underlying="COIN",
        expiration=date(2026, 2, 20),
        strike=Decimal("200"),
        option_type=OptionType.CALL,
    )

    book.apply(
        option_trade(
            side=Side.BUY,
            position_effect=PositionEffect.CLOSE,
            quantity=Decimal("5"),
        )
    )

    book.apply(
        stock_trade(
            side=Side.SELL,
            position_effect=PositionEffect.CLOSE,
            quantity=Decimal("-500"),
        )
    )

    assert book.quantity(contract) == Decimal("0")
    assert book.quantity(Instrument("COIN")) == Decimal("0")
    assert book.started_before_data(contract)
    assert book.started_before_data(Instrument("COIN"))

def test_partial_close_reduces_known_position() -> None:
    book = PositionBook()

    book.apply(
        option_trade(
            side=Side.SELL,
            position_effect=PositionEffect.OPEN,
            quantity=Decimal("-5"),
        )
    )

    book.apply(
        option_trade(
            side=Side.BUY,
            position_effect=PositionEffect.CLOSE,
            quantity=Decimal("2"),
        )
    )

    contract = OptionContract(
        underlying="COIN",
        expiration=date(2026, 2, 20),
        strike=Decimal("200"),
        option_type=OptionType.CALL,
    )

    assert book.quantity(contract) == Decimal("-3")
    assert not book.started_before_data(contract)

def test_partial_close_of_position_started_before_data_remains_unknown() -> None:
    book = PositionBook()

    contract = OptionContract(
        underlying="COIN",
        expiration=date(2026, 2, 20),
        strike=Decimal("200"),
        option_type=OptionType.CALL,
    )

    book.apply(
        option_trade(
            side=Side.BUY,
            position_effect=PositionEffect.CLOSE,
            quantity=Decimal("2"),
        )
    )

    assert book.started_before_data(contract)

def test_started_before_data_position_has_unknown_quantity() -> None:
    book = PositionBook()

    contract = OptionContract(
        underlying="COIN",
        expiration=date(2026, 2, 20),
        strike=Decimal("200"),
        option_type=OptionType.CALL,
    )

    book.apply(
        option_trade(
            side=Side.BUY,
            position_effect=PositionEffect.CLOSE,
            quantity=Decimal("2"),
        )
    )

    state = book.state(contract)

    assert state.quantity_known is False
    assert state.started_before_data is True

def test_repeated_close_of_position_started_before_data_remains_unknown() -> None:
    book = PositionBook()

    contract = OptionContract(
        underlying="COIN",
        expiration=date(2026, 2, 20),
        strike=Decimal("200"),
        option_type=OptionType.CALL,
    )

    book.apply(
        option_trade(
            side=Side.BUY,
            position_effect=PositionEffect.CLOSE,
            quantity=Decimal("2"),
        )
    )

    book.apply(
        option_trade(
            side=Side.BUY,
            position_effect=PositionEffect.CLOSE,
            quantity=Decimal("3"),
        )
    )

    state = book.state(contract)

    assert state.quantity_known is False
    assert state.started_before_data is True

