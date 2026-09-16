from datetime import datetime
from decimal import Decimal

from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.forex_pair import ForexPair


def forex_trade(
    pair: str,
    *,
    side: Side,
    effect: PositionEffect,
    at: datetime,
) -> Trade:
    return Trade(
        legs=(
            Leg(
                instrument=ForexPair.from_symbol(pair),
                side=side,
                position_effect=effect,
                executions=(
                    Execution(
                        quantity=Decimal("10000"),
                        execution_price=Decimal("1.1000"),
                        executed_at=at,
                    ),
                ),
            ),
        ),
    )


def test_forex_open_and_close_reconstruct_into_one_campaign() -> None:
    opened = forex_trade(
        "EUR/USD",
        side=Side.BUY,
        effect=PositionEffect.OPEN,
        at=datetime(2026, 8, 3, 10, 0),
    )
    closed = forex_trade(
        "EUR/USD",
        side=Side.SELL,
        effect=PositionEffect.CLOSE,
        at=datetime(2026, 8, 10, 10, 0),
    )

    campaigns = CampaignReconstructor().reconstruct([opened, closed])

    assert len(campaigns) == 1
    assert campaigns[0].trades == (opened, closed)


def test_different_forex_pairs_are_different_campaigns() -> None:
    eurusd = forex_trade(
        "EUR/USD",
        side=Side.BUY,
        effect=PositionEffect.OPEN,
        at=datetime(2026, 8, 3, 10, 0),
    )
    usdjpy = forex_trade(
        "USD/JPY",
        side=Side.BUY,
        effect=PositionEffect.OPEN,
        at=datetime(2026, 8, 3, 10, 1),
    )

    campaigns = CampaignReconstructor().reconstruct([eurusd, usdjpy])

    assert len(campaigns) == 2
    for campaign in campaigns:
        pairs = {
            leg.instrument.symbol
            for trade in campaign.trades
            for leg in trade.legs
            if isinstance(leg.instrument, ForexPair)
        }
        assert len(pairs) == 1
