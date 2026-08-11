"""End-to-end test of the Thinkorswim options-to-campaign pipeline."""

from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.importers.thinkorswim.trade_history_reader import (
    ThinkorswimTradeHistoryReader,
)
from campaigniq.importers.thinkorswim.translator import to_trade
from campaigniq.sources.thinkorswim.source_reader import (
    ThinkorswimSourceReader,
)


def test_thinkorswim_options_can_be_reconstructed_into_campaigns() -> None:
    filename = "tests/data/thinkorswim/Account Trading History.csv"

    statement = ThinkorswimSourceReader().read(filename)
    section = statement.section("Account Trade History")

    orders = ThinkorswimTradeHistoryReader().read(section)

    option_orders = [
        order
        for order in orders
        if all(
            row.option_type.upper() in {"CALL", "PUT"}
            for row in order.legs
        )
    ]

    trades = [to_trade(order) for order in option_orders]

    campaigns = CampaignReconstructor().reconstruct(trades)

    assert len(orders) == 67
    assert len(option_orders) == 27
    assert len(trades) == 27
    assert campaigns
    assert all(campaign.trades for campaign in campaigns)
    