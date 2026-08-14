from datetime import datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_activity import PositionActivity
from campaigniq.domain.position_activity_builder import PositionActivityBuilder
from campaigniq.domain.position_activity_kind import PositionActivityKind
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade


EXPIRATION = datetime(2025, 3, 20).date()


def iron_condor_trade(
    *,
    quantity: str,
    executed_at: datetime,
    position_effect: PositionEffect = PositionEffect.OPEN,
) -> Trade:
    quantity_decimal = Decimal(quantity)

    legs = (
        OptionLeg(
            contract=OptionContract(
                underlying="SPX",
                expiration=EXPIRATION,
                strike=Decimal("5650"),
                option_type=OptionType.CALL,
            ),
            side=(
                Side.SELL
                if position_effect == PositionEffect.OPEN
                else Side.BUY
            ),
            position_effect=position_effect,
            executions=(
                Execution(
                    quantity=quantity_decimal,
                    execution_price=Decimal("13.48"),
                    executed_at=executed_at,
                ),
            ),
            broker_strategy="IRON CONDOR",
        ),
        OptionLeg(
            contract=OptionContract(
                underlying="SPX",
                expiration=EXPIRATION,
                strike=Decimal("5670"),
                option_type=OptionType.CALL,
            ),
            side=(
                Side.BUY
                if position_effect == PositionEffect.OPEN
                else Side.SELL
            ),
            position_effect=position_effect,
            executions=(
                Execution(
                    quantity=-quantity_decimal,
                    execution_price=Decimal("8.75"),
                    executed_at=executed_at,
                ),
            ),
            broker_strategy="IRON CONDOR",
        ),
        OptionLeg(
            contract=OptionContract(
                underlying="SPX",
                expiration=EXPIRATION,
                strike=Decimal("5555"),
                option_type=OptionType.PUT,
            ),
            side=(
                Side.SELL
                if position_effect == PositionEffect.OPEN
                else Side.BUY
            ),
            position_effect=position_effect,
            executions=(
                Execution(
                    quantity=quantity_decimal,
                    execution_price=Decimal("17.43"),
                    executed_at=executed_at,
                ),
            ),
            broker_strategy="IRON CONDOR",
        ),
        OptionLeg(
            contract=OptionContract(
                underlying="SPX",
                expiration=EXPIRATION,
                strike=Decimal("5535"),
                option_type=OptionType.PUT,
            ),
            side=(
                Side.BUY
                if position_effect == PositionEffect.OPEN
                else Side.SELL
            ),
            position_effect=position_effect,
            executions=(
                Execution(
                    quantity=-quantity_decimal,
                    execution_price=Decimal("12.31"),
                    executed_at=executed_at,
                ),
            ),
            broker_strategy="IRON CONDOR",
        ),
    )

    return Trade(legs=legs)


def test_builder_groups_same_structure() -> None:
    first = iron_condor_trade(
        quantity="-3",
        executed_at=datetime(2025, 3, 11, 7, 34, 47),
    )

    second = iron_condor_trade(
        quantity="-2",
        executed_at=datetime(2025, 3, 11, 7, 34, 47),
    )

    activities = PositionActivityBuilder().build([first, second])

    assert len(activities) == 1

    activity = activities[0]

    assert isinstance(activity, PositionActivity)
    assert activity.kind == PositionActivityKind.OPEN
    assert activity.trades == (first, second)


def test_builder_groups_many_identical_orders() -> None:
    trades = tuple(
        iron_condor_trade(
            quantity="-1",
            executed_at=datetime(2025, 3, 17, 7, 58, 53),
        )
        for _ in range(5)
    )

    activities = PositionActivityBuilder().build(list(trades))

    assert len(activities) == 1
    assert activities[0].trades == trades


def test_builder_does_not_group_different_structures() -> None:
    first = iron_condor_trade(
        quantity="-3",
        executed_at=datetime(2025, 3, 11, 7, 34, 47),
    )

    second = iron_condor_trade(
        quantity="-2",
        executed_at=datetime(2025, 3, 12, 7, 36, 2),
    )

    second_leg = second.legs[0]

    changed_leg = OptionLeg(
        contract=OptionContract(
            underlying="SPX",
            expiration=second_leg.contract.expiration,
            strike=Decimal("5675"),
            option_type=second_leg.contract.option_type,
        ),
        side=second_leg.side,
        position_effect=second_leg.position_effect,
        executions=second_leg.executions,
        broker_strategy=second_leg.broker_strategy,
    )

    second = Trade(
        legs=(changed_leg,) + second.legs[1:]
    )

    activities = PositionActivityBuilder().build([first, second])

    assert len(activities) == 2
    assert activities[0].trades == (first,)
    assert activities[1].trades == (second,)


def test_builder_activity_preserves_aggregate_quantity_per_contract() -> None:
    first = iron_condor_trade(
        quantity="-3",
        executed_at=datetime(2025, 3, 11, 7, 34, 47),
    )

    second = iron_condor_trade(
        quantity="-2",
        executed_at=datetime(2025, 3, 11, 7, 34, 47),
    )

    activities = PositionActivityBuilder().build([first, second])

    assert len(activities) == 1

    activity = activities[0]

    quantities = [
        sum(
            (
                trade.legs[index].quantity
                for trade in activity.trades
            ),
            Decimal("0"),
        )
        for index in range(4)
    ]

    assert quantities == [
        Decimal("-5"),
        Decimal("5"),
        Decimal("-5"),
        Decimal("5"),
    ]


def test_builder_preserves_partial_close_as_partial_activity() -> None:
    close = iron_condor_trade(
        quantity="4",
        executed_at=datetime(2025, 3, 24, 9, 30, 0),
        position_effect=PositionEffect.CLOSE,
    )

    activities = PositionActivityBuilder().build([close])

    assert len(activities) == 1

    activity = activities[0]

    assert activity.kind == PositionActivityKind.CLOSE
    assert activity.trades == (close,)

    quantities = [
        trade_leg.quantity
        for trade_leg in activity.trades[0].legs
    ]

    assert quantities == [
        Decimal("4"),
        Decimal("-4"),
        Decimal("4"),
        Decimal("-4"),
    ]
