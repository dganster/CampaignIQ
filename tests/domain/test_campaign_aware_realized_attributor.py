from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.leg import Leg
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument


def _trade(
    side: Side,
    effect: PositionEffect,
    quantity: str,
    price: str,
    when: datetime,
) -> Trade:
    return Trade(
        legs=(
            InstrumentLeg(
                instrument=Instrument("IBM"),
                side=side,
                position_effect=effect,
                executions=(
                    Execution(
                        quantity=Decimal(quantity),
                        execution_price=Decimal(price),
                        executed_at=when,
                    ),
                ),
            ),
        ),
    )


def _record() -> RealizedGainLossRecord:
    return RealizedGainLossRecord(
        closed_date=date(2026, 1, 10),
        instrument=Instrument("IBM"),
        quantity=Decimal("100"),
        closing_price=Decimal("210"),
        proceeds=Decimal("21000"),
        cost_basis=Decimal("20000"),
        gain_loss=Decimal("1000"),
        basis_method="FIFO",
        term="SHORT TERM",
    )


def test_attribute_campaigns_preserves_campaign_id() -> None:
    opening = _trade(
        Side.BUY,
        PositionEffect.OPEN,
        "100",
        "200",
        datetime(2026, 1, 5, 10, 0),
    )
    closing = _trade(
        Side.SELL,
        PositionEffect.CLOSE,
        "100",
        "210",
        datetime(2026, 1, 10, 10, 0),
    )

    campaign = Campaign(
        campaign_id="CAMP-000001",
        trades=(opening, closing),
    )

    result = RealizedLotAttributor().attribute_campaigns(
        [campaign],
        [_record()],
    )

    assert result[0].allocations[0].campaign_id == "CAMP-000001"


def test_legacy_attribute_has_no_campaign_provenance() -> None:
    opening = _trade(
        Side.BUY,
        PositionEffect.OPEN,
        "100",
        "200",
        datetime(2026, 1, 5, 10, 0),
    )
    closing = _trade(
        Side.SELL,
        PositionEffect.CLOSE,
        "100",
        "210",
        datetime(2026, 1, 10, 10, 0),
    )

    result = RealizedLotAttributor().attribute(
        [opening, closing],
        [_record()],
    )

    assert result[0].allocations[0].campaign_id is None
