from datetime import datetime
from decimal import Decimal

from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.domain.execution import Execution
from campaigniq.domain.forex_settlement_attribution import (
    FOREX_TRANSACTION_REPORT_CLOCK_OFFSET,
    attribute_forex_settlements,
    forex_transaction_report_trade_time_on_statement_clock,
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
    closed = trade("EUR/USD", Side.SELL, PositionEffect.CLOSE, datetime(2026,8,27,18))
    campaign = CampaignReconstructor().reconstruct([opened, closed])[0]

    result = attribute_forex_settlements([campaign], [settlement()])

    assert len(result) == 1
    assert result[0].campaign_id == campaign.campaign_id
    assert result[0].gain_loss == Decimal("3.00")


def test_does_not_attribute_different_currency_pair():
    opened = trade("USD/JPY", Side.BUY, PositionEffect.OPEN, datetime(2026,8,20,10))
    closed = trade("USD/JPY", Side.SELL, PositionEffect.CLOSE, datetime(2026,8,27,18))
    campaign = CampaignReconstructor().reconstruct([opened, closed])[0]

    assert attribute_forex_settlements([campaign], [settlement()]) == ()


def test_does_not_attribute_without_close_evidence():
    opened = trade("EUR/USD", Side.BUY, PositionEffect.OPEN, datetime(2026,8,20,10))
    campaign = CampaignReconstructor().reconstruct([opened])[0]

    assert attribute_forex_settlements([campaign], [settlement()]) == ()


def test_source_clock_normalization_handles_midnight_crossing() -> None:
    report_trade_at = datetime(2026, 4, 14, 0, 27, 27)

    assert FOREX_TRANSACTION_REPORT_CLOCK_OFFSET.total_seconds() == 2 * 60 * 60
    assert forex_transaction_report_trade_time_on_statement_clock(
        settlement(trade_at=report_trade_at)
    ) == datetime(2026, 4, 13, 22, 27, 27)


def test_midnight_crossing_settlement_attributes_to_statement_close() -> None:
    opened = trade(
        "EUR/USD",
        Side.SELL,
        PositionEffect.OPEN,
        datetime(2026, 4, 13, 14, 13, 37),
    )
    closed = trade(
        "EUR/USD",
        Side.BUY,
        PositionEffect.CLOSE,
        datetime(2026, 4, 13, 22, 27, 27),
    )
    campaign = CampaignReconstructor().reconstruct([opened, closed])[0]

    result = attribute_forex_settlements(
        [campaign],
        [
            settlement(
                pair="EUR/USD",
                trade_at=datetime(2026, 4, 14, 0, 27, 27),
                pnl="-186.00",
            )
        ],
    )

    assert len(result) == 1
    assert result[0].campaign_id == campaign.campaign_id
    assert result[0].gain_loss == Decimal("-186.00")
