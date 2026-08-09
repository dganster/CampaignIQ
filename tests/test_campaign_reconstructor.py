from datetime import date, datetime
from decimal import Decimal
from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade


def test_empty_trade_list_returns_no_campaigns():
    reconstructor = CampaignReconstructor()

    campaigns = reconstructor.reconstruct([])

    assert campaigns == []


def test_single_trade_creates_single_campaign():
    contract = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )

    leg = OptionLeg(
        contract=contract,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("3.25"),
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="SINGLE",
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

    leg1 = OptionLeg(
        contract=contract1,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("3.25"),
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="SINGLE",
    )

    trade1 = Trade(legs=(leg1,))

    contract2 = OptionContract(
        underlying="MSFT",
        expiration=date(2026, 8, 21),
        strike=Decimal("500"),
        option_type=OptionType.CALL,
    )

    leg2 = OptionLeg(
        contract=contract2,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("5.00"),
        executed_at=datetime(2026, 7, 29, 11, 30),
        broker_strategy="SINGLE",
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

    leg1 = OptionLeg(
        contract=contract1,
        side=Side.SELL,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("5.00"),
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="VERTICAL",
    )

    contract2 = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("260"),
        option_type=OptionType.CALL,
    )

    leg2 = OptionLeg(
        contract=contract2,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("2.50"),
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

    leg1 = OptionLeg(
        contract=contract1,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("3.25"),
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="SINGLE",
    )

    trade1 = Trade(legs=(leg1,))

    contract2 = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("260"),
        option_type=OptionType.CALL,
    )

    leg2 = OptionLeg(
        contract=contract2,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("2.75"),
        executed_at=datetime(2026, 7, 30, 10, 30),
        broker_strategy="SINGLE",
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

    leg1 = OptionLeg(
        contract=contract1,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("3.25"),
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="SINGLE",
    )

    trade1 = Trade(legs=(leg1,))

    contract2 = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 28),
        strike=Decimal("260"),
        option_type=OptionType.CALL,
    )

    leg2 = OptionLeg(
        contract=contract2,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        quantity=Decimal("1"),
        execution_price=Decimal("2.75"),
        executed_at=datetime(2026, 8, 29, 10, 30),
        broker_strategy="SINGLE",
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

        opening_leg = OptionLeg(
            contract=contract1,
            side=Side.BUY,
            position_effect=PositionEffect.OPEN,
            quantity=Decimal("1"),
            execution_price=Decimal("3.25"),
            executed_at=datetime(2026, 7, 29, 10, 30),
            broker_strategy="SINGLE",
        )

        opening_trade = Trade(legs=(opening_leg,))

        closing_leg = OptionLeg(
            contract=contract1,
            side=Side.SELL,
            position_effect=PositionEffect.CLOSE,
            quantity=Decimal("1"),
            execution_price=Decimal("4.50"),
            executed_at=datetime(2026, 8, 3, 11, 15),
            broker_strategy="SINGLE",
        )

        closing_trade = Trade(legs=(closing_leg,))

        contract2 = OptionContract(
            underlying="IBM",
            expiration=date(2026, 8, 21),
            strike=Decimal("260"),
            option_type=OptionType.CALL,
        )

        reopening_leg = OptionLeg(
            contract=contract2,
            side=Side.BUY,
            position_effect=PositionEffect.OPEN,
            quantity=Decimal("1"),
            execution_price=Decimal("2.75"),
            executed_at=datetime(2026, 8, 10, 10, 30),
            broker_strategy="SINGLE",
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
        
