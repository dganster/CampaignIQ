
from datetime import date, datetime
from decimal import Decimal

from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade

def test_open_and_close_are_one_campaign() -> None:
    contract = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("250"),
        option_type=OptionType.CALL,
    )
    opening_leg = OptionLeg(
        contract=contract,
        side=Side.BUY,
        quantity=Decimal("1"),
        execution_price=Decimal("3.25"),
        executed_at=datetime(2026, 7, 29, 10, 30),
        broker_strategy="SINGLE",
    )
    opening_trade = Trade(legs=(opening_leg,))

    closing_leg = OptionLeg(
        contract=contract,
        side=Side.SELL,
        quantity=Decimal("1"),
        execution_price=Decimal("4.50"),
        executed_at=datetime(2026, 8, 3, 11, 15),
        broker_strategy="SINGLE",
    )

    closing_trade = Trade(legs=(closing_leg,))

    reconstructor = CampaignReconstructor()

    campaigns = reconstructor.reconstruct([opening_trade, closing_trade])

    assert len(campaigns) == 1
    assert campaigns[0].trades == (opening_trade, closing_trade)
    assert campaigns[0].trades[0] == opening_trade
    assert campaigns[0].trades[1] == closing_trade
    assert campaigns[0].trades == (opening_trade, closing_trade)
    