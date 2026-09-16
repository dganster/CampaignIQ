from datetime import datetime
from decimal import Decimal

from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.domain.execution import Execution
from campaigniq.domain.forex_settlement_attribution import (
    attribute_forex_settlements,
)
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.forex_pair import ForexPair
from campaigniq.importers.schwab.forex_transaction_reader import SchwabForexSettlement


def trade(pair, side, effect, at):
    return Trade(legs=(InstrumentLeg(
        instrument=ForexPair.from_symbol(pair),
        side=side,
        position_effect=effect,
        executions=(Execution(
            quantity=Decimal("100000"),
            execution_price=Decimal("1.16508"),
            executed_at=at,
        ),),
    ),))


def settlement(pair="EUR/USD", trade_at=datetime(2026, 8, 27, 20, 19, 57), pnl="3.00"):
    return SchwabForexSettlement(
        order_id="1007745626983",
        trade_at=trade_at,
        settlement_at=datetime(2026, 8, 28, 17, 0),
        instrument=pair,
        side="Sell",
        rate=Decimal("1.16508"),
        amount=Decimal("-100000"),
        settlement_pl_usd=Decimal(pnl),
        total_position=Decimal("0"),
    )


def test_attributes_settlement_to_matching_forex_campaign():
    opened = trade("EUR/USD", Side.BUY, PositionEffect.OPEN, datetime(2026,8,20,10))
    closed = trade("EUR/USD", Side.SELL, PositionEffect.CLOSE, datetime(2026,8,27,20))
    campaign = CampaignReconstructor().reconstruct([opened, closed])[0]

    result = attribute_forex_settlements([campaign], [settlement()])

    assert len(result) == 1
    assert result[0].campaign_id == campaign.campaign_id
    assert result[0].gain_loss == Decimal("3.00")


def test_does_not_attribute_different_currency_pair():
    opened = trade("USD/JPY", Side.BUY, PositionEffect.OPEN, datetime(2026,8,20,10))
    closed = trade("USD/JPY", Side.SELL, PositionEffect.CLOSE, datetime(2026,8,27,20))
    campaign = CampaignReconstructor().reconstruct([opened, closed])[0]

    assert attribute_forex_settlements([campaign], [settlement()]) == ()


def test_does_not_attribute_without_close_evidence():
    opened = trade("EUR/USD", Side.BUY, PositionEffect.OPEN, datetime(2026,8,20,10))
    campaign = CampaignReconstructor().reconstruct([opened])[0]

    assert attribute_forex_settlements([campaign], [settlement()]) == ()
