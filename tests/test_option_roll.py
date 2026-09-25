from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_roll import detect_option_rolls
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade


WHEN = datetime(2026, 3, 10, 12, 2, 32)


def leg(
    *,
    expiration: date,
    strike: str,
    option_type: OptionType = OptionType.CALL,
    side: Side,
    effect: PositionEffect,
    quantity: str,
    symbol: str = "NFLX",
) -> OptionLeg:
    return OptionLeg(
        contract=OptionContract(
            underlying=symbol,
            expiration=expiration,
            strike=Decimal(strike),
            option_type=option_type,
        ),
        side=side,
        position_effect=effect,
        executions=(
            Execution(
                quantity=Decimal(quantity),
                execution_price=Decimal("1"),
                executed_at=WHEN,
            ),
        ),
        broker_strategy="CALENDAR",
    )


def short_call_roll() -> Trade:
    return Trade(
        legs=(
            leg(
                expiration=date(2026, 4, 17),
                strike="74",
                side=Side.SELL,
                effect=PositionEffect.OPEN,
                quantity="-50",
            ),
            leg(
                expiration=date(2026, 3, 20),
                strike="74",
                side=Side.BUY,
                effect=PositionEffect.CLOSE,
                quantity="50",
            ),
        )
    )


def test_detects_same_trade_short_call_roll() -> None:
    trade = short_call_roll()

    rolls = detect_option_rolls(trade)

    assert len(rolls) == 1
    roll = rolls[0]
    assert roll.underlying == "NFLX"
    assert roll.closed_contract.expiration == date(2026, 3, 20)
    assert roll.opened_contract.expiration == date(2026, 4, 17)
    assert roll.quantity == Decimal("50")
    assert roll.expiration_changed
    assert not roll.strike_changed


def test_preserves_original_broker_legs() -> None:
    trade = short_call_roll()

    roll = detect_option_rolls(trade)[0]

    assert roll.closed_leg is trade.legs[1]
    assert roll.opened_leg is trade.legs[0]
    assert roll.closed_leg.side is Side.BUY
    assert roll.opened_leg.side is Side.SELL


def test_detects_long_option_roll() -> None:
    trade = Trade(
        legs=(
            leg(
                expiration=date(2026, 3, 20),
                strike="74",
                side=Side.SELL,
                effect=PositionEffect.CLOSE,
                quantity="-5",
            ),
            leg(
                expiration=date(2026, 4, 17),
                strike="74",
                side=Side.BUY,
                effect=PositionEffect.OPEN,
                quantity="5",
            ),
        )
    )

    assert len(detect_option_rolls(trade)) == 1


def test_quantity_mismatch_is_not_a_roll() -> None:
    trade = Trade(
        legs=(
            leg(
                expiration=date(2026, 3, 20),
                strike="74",
                side=Side.BUY,
                effect=PositionEffect.CLOSE,
                quantity="50",
            ),
            leg(
                expiration=date(2026, 4, 17),
                strike="74",
                side=Side.SELL,
                effect=PositionEffect.OPEN,
                quantity="-40",
            ),
        )
    )

    assert detect_option_rolls(trade) == ()


def test_different_underlying_is_not_a_roll() -> None:
    trade = Trade(
        legs=(
            leg(
                expiration=date(2026, 3, 20),
                strike="74",
                side=Side.BUY,
                effect=PositionEffect.CLOSE,
                quantity="50",
            ),
            leg(
                expiration=date(2026, 4, 17),
                strike="74",
                side=Side.SELL,
                effect=PositionEffect.OPEN,
                quantity="-50",
                symbol="AAPL",
            ),
        )
    )

    assert detect_option_rolls(trade) == ()


def test_different_option_type_is_not_a_roll() -> None:
    trade = Trade(
        legs=(
            leg(
                expiration=date(2026, 3, 20),
                strike="74",
                option_type=OptionType.CALL,
                side=Side.BUY,
                effect=PositionEffect.CLOSE,
                quantity="50",
            ),
            leg(
                expiration=date(2026, 4, 17),
                strike="74",
                option_type=OptionType.PUT,
                side=Side.SELL,
                effect=PositionEffect.OPEN,
                quantity="-50",
            ),
        )
    )

    assert detect_option_rolls(trade) == ()


def test_long_to_short_direction_change_is_not_a_roll() -> None:
    trade = Trade(
        legs=(
            leg(
                expiration=date(2026, 3, 20),
                strike="74",
                side=Side.SELL,
                effect=PositionEffect.CLOSE,
                quantity="-50",
            ),
            leg(
                expiration=date(2026, 4, 17),
                strike="74",
                side=Side.SELL,
                effect=PositionEffect.OPEN,
                quantity="-50",
            ),
        )
    )

    assert detect_option_rolls(trade) == ()


def test_identical_contract_close_and_reopen_is_not_classified_as_roll() -> None:
    trade = Trade(
        legs=(
            leg(
                expiration=date(2026, 3, 20),
                strike="74",
                side=Side.BUY,
                effect=PositionEffect.CLOSE,
                quantity="50",
            ),
            leg(
                expiration=date(2026, 3, 20),
                strike="74",
                side=Side.SELL,
                effect=PositionEffect.OPEN,
                quantity="-50",
            ),
        )
    )

    assert detect_option_rolls(trade) == ()


def test_strike_change_is_reported() -> None:
    trade = Trade(
        legs=(
            leg(
                expiration=date(2026, 3, 20),
                strike="78",
                side=Side.BUY,
                effect=PositionEffect.CLOSE,
                quantity="50",
            ),
            leg(
                expiration=date(2026, 4, 17),
                strike="74",
                side=Side.SELL,
                effect=PositionEffect.OPEN,
                quantity="-50",
            ),
        )
    )

    roll = detect_option_rolls(trade)[0]

    assert roll.expiration_changed
    assert roll.strike_changed


def test_single_open_leg_is_not_a_roll() -> None:
    trade = Trade(
        legs=(
            leg(
                expiration=date(2026, 4, 17),
                strike="74",
                side=Side.SELL,
                effect=PositionEffect.OPEN,
                quantity="-50",
            ),
        )
    )

    assert detect_option_rolls(trade) == ()


def test_single_close_leg_is_not_a_roll() -> None:
    trade = Trade(
        legs=(
            leg(
                expiration=date(2026, 3, 20),
                strike="74",
                side=Side.BUY,
                effect=PositionEffect.CLOSE,
                quantity="50",
            ),
        )
    )

    assert detect_option_rolls(trade) == ()


def test_ambiguous_many_to_many_pairing_is_omitted() -> None:
    trade = Trade(
        legs=(
            leg(
                expiration=date(2026, 3, 20),
                strike="70",
                side=Side.BUY,
                effect=PositionEffect.CLOSE,
                quantity="50",
            ),
            leg(
                expiration=date(2026, 3, 20),
                strike="74",
                side=Side.BUY,
                effect=PositionEffect.CLOSE,
                quantity="50",
            ),
            leg(
                expiration=date(2026, 4, 17),
                strike="76",
                side=Side.SELL,
                effect=PositionEffect.OPEN,
                quantity="-50",
            ),
            leg(
                expiration=date(2026, 4, 17),
                strike="78",
                side=Side.SELL,
                effect=PositionEffect.OPEN,
                quantity="-50",
            ),
        )
    )

    assert detect_option_rolls(trade) == ()
