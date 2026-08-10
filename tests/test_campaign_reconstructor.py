from datetime import date, datetime
from decimal import Decimal

from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.domain.directional_bias import DirectionalBias
from campaigniq.domain.execution import Execution
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade


def make_execution(
    quantity: str,
    price: str,
    executed_at: datetime,
) -> Execution:
    return Execution(
        quantity=Decimal(quantity),
        execution_price=Decimal(price),
        executed_at=executed_at,
    )


def make_leg(
    contract: OptionContract,
    side: Side,
    position_effect: PositionEffect,
    quantity: str,
    price: str,
    executed_at: datetime,
    broker_strategy: str = "SINGLE",
) -> OptionLeg:
    execution = make_execution(
        quantity=quantity,
        price=price,
        executed_at=executed_at,
    )

    return OptionLeg(
        contract=contract,
        side=side,
        position_effect=position_effect,
        executions=(execution,),
        broker_strategy=broker_strategy,
    )


def test_single_trade_creates_single_campaign():
    contract = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    leg = make_leg(
        contract=contract,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="3.25",
        executed_at=datetime(2026, 7, 29, 10, 30),
    )

    trade = Trade(legs=(leg,))

    reconstructor = CampaignReconstructor()

    campaigns = reconstructor.reconstruct([trade])

    assert len(campaigns) == 1
    assert campaigns[0].trades == (trade,)


def test_two_unrelated_trades_create_two_campaigns():
    contract1 = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    leg1 = make_leg(
        contract=contract1,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="3.25",
        executed_at=datetime(2026, 7, 29, 10, 30),
    )

    trade1 = Trade(legs=(leg1,))

    contract2 = OptionContract(
        underlying="AAPL",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    leg2 = make_leg(
        contract=contract2,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="3.25",
        executed_at=datetime(2026, 7, 29, 10, 30),
    )

    trade2 = Trade(legs=(leg2,))

    reconstructor = CampaignReconstructor()

    campaigns = reconstructor.reconstruct([trade1, trade2])

    assert len(campaigns) == 2
    assert campaigns[0].trades == (trade1,)
    assert campaigns[1].trades == (trade2,)


def test_multi_leg_trade_creates_single_campaign():
    contract1 = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    contract2 = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("260"),
        option_type=OptionType.CALL,
    )

    leg1 = make_leg(
        contract=contract1,
        side=Side.SELL,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="5.00",
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="VERTICAL",
    )

    leg2 = make_leg(
        contract=contract2,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="2.50",
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="VERTICAL",
    )

    trade = Trade(legs=(leg1, leg2))

    reconstructor = CampaignReconstructor()

    campaigns = reconstructor.reconstruct([trade])

    assert len(campaigns) == 1
    assert campaigns[0].trades == (trade,)


def test_same_underlying_different_contracts_can_be_one_campaign():
    contract1 = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    leg1 = make_leg(
        contract=contract1,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="3.25",
        executed_at=datetime(2026, 7, 29, 10, 30),
    )

    trade1 = Trade(legs=(leg1,))

    contract2 = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("260"),
        option_type=OptionType.CALL,
    )

    leg2 = make_leg(
        contract=contract2,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="2.75",
        executed_at=datetime(2026, 8, 10, 10, 30),
    )

    trade2 = Trade(legs=(leg2,))

    reconstructor = CampaignReconstructor()

    campaigns = reconstructor.reconstruct([trade1, trade2])

    assert len(campaigns) == 1
    assert campaigns[0].trades == (trade1, trade2)


def test_same_underlying_more_than_thirty_days_apart_creates_two_campaigns():
    contract1 = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    leg1 = make_leg(
        contract=contract1,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="3.25",
        executed_at=datetime(2026, 7, 29, 10, 30),
    )

    trade1 = Trade(legs=(leg1,))

    contract2 = OptionContract(
        underlying="IBM",
        expiration=date(2026, 9, 18),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    leg2 = make_leg(
        contract=contract2,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="3.25",
        executed_at=datetime(2026, 9, 1, 10, 30),
    )

    trade2 = Trade(legs=(leg2,))

    reconstructor = CampaignReconstructor()

    campaigns = reconstructor.reconstruct([trade1, trade2])

    assert len(campaigns) == 2
    assert campaigns[0].trades == (trade1,)
    assert campaigns[1].trades == (trade2,)


def test_close_and_reopen_without_sentiment_change_are_one_campaign():
    contract1 = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    opening_leg = make_leg(
        contract=contract1,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="3.25",
        executed_at=datetime(2026, 7, 29, 10, 30),
    )

    opening_trade = Trade(legs=(opening_leg,))

    closing_leg = make_leg(
        contract=contract1,
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE,
        quantity="1",
        price="4.50",
        executed_at=datetime(2026, 8, 3, 11, 15),
    )

    closing_trade = Trade(legs=(closing_leg,))

    contract2 = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("260"),
        option_type=OptionType.CALL,
    )

    reopening_leg = make_leg(
        contract=contract2,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="2.75",
        executed_at=datetime(2026, 8, 10, 10, 30),
    )

    reopening_trade = Trade(legs=(reopening_leg,))

    reconstructor = CampaignReconstructor()

    campaigns = reconstructor.reconstruct(
        [opening_trade, closing_trade, reopening_trade]
    )

    assert len(campaigns) == 1
    assert campaigns[0].trades == (
        opening_trade,
        closing_trade,
        reopening_trade,
    )


def test_bullish_to_bearish_change_creates_new_campaign():
    call_contract = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    opening_call_leg = make_leg(
        contract=call_contract,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="3.25",
        executed_at=datetime(2026, 7, 29, 10, 30),
    )

    opening_call_trade = Trade(legs=(opening_call_leg,))

    closing_call_leg = make_leg(
        contract=call_contract,
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE,
        quantity="1",
        price="4.50",
        executed_at=datetime(2026, 8, 3, 11, 15),
    )

    closing_call_trade = Trade(legs=(closing_call_leg,))

    put_contract = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.PUT,
    )

    opening_put_leg = make_leg(
        contract=put_contract,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity="1",
        price="3.00",
        executed_at=datetime(2026, 8, 10, 10, 30),
    )

    opening_put_trade = Trade(legs=(opening_put_leg,))

    reconstructor = CampaignReconstructor()

    campaigns = reconstructor.reconstruct(
        [
            opening_call_trade,
            closing_call_trade,
            opening_put_trade,
        ]
    )

    assert len(campaigns) == 2
    assert campaigns[0].trades == (
        opening_call_trade,
        closing_call_trade,
    )
    assert campaigns[1].trades == (opening_put_trade,)
    