from datetime import datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.lot_book import LotBook


def _opening_trade() -> Trade:
    return Trade(
        legs=(
            Leg(
                instrument=Instrument("IBM"),
                side=Side.BUY,
                position_effect=PositionEffect.OPEN,
                executions=(
                    Execution(
                        quantity=Decimal("100"),
                        execution_price=Decimal("200"),
                        executed_at=datetime(2026, 1, 5, 10, 0),
                    ),
                ),
            ),
        )
    )


def test_opening_lot_records_campaign_id() -> None:
    book = LotBook()

    book.apply_trade(_opening_trade(), campaign_id="CAMP-000042")

    lots = book.lots(Instrument("IBM"))

    assert len(lots) == 1
    assert lots[0].campaign_id == "CAMP-000042"


def test_campaign_id_survives_partial_close() -> None:
    book = LotBook()
    book.apply_trade(_opening_trade(), campaign_id="CAMP-000042")

    close_trade = Trade(
        legs=(
            Leg(
                instrument=Instrument("IBM"),
                side=Side.SELL,
                position_effect=PositionEffect.CLOSE,
                executions=(
                    Execution(
                        quantity=Decimal("40"),
                        execution_price=Decimal("210"),
                        executed_at=datetime(2026, 1, 10, 10, 0),
                    ),
                ),
            ),
        )
    )

    allocations = book.apply_trade(close_trade)

    assert allocations[0].lot_id == "LOT-000001"
    remaining = book.lots(Instrument("IBM"))[0]
    assert remaining.quantity == Decimal("60")
    assert remaining.campaign_id == "CAMP-000042"
