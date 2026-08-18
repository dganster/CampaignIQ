from datetime import datetime
from decimal import Decimal

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.leg import Leg
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument


def _close_trade(
    symbol: str,
    quantity: str,
    when: datetime,
) -> Trade:
    return Trade(
        legs=(
            InstrumentLeg(
                instrument=Instrument(symbol),
                side=Side.SELL,
                position_effect=PositionEffect.CLOSE,
                executions=(
                    Execution(
                        quantity=Decimal(quantity),
                        execution_price=Decimal("100"),
                        executed_at=when,
                    ),
                ),
            ),
        ),
    )


def test_boundary_campaign_can_claim_exact_preperiod_lot() -> None:
    historical_lot = Lot(
        lot_id="DEC-IBM",
        instrument=Instrument("IBM"),
        quantity=Decimal("100"),
        opened_at=datetime(2025, 12, 31, 16, 0),
        basis_total=Decimal("9000"),
        basis_source="DECEMBER_SNAPSHOT",
    )

    campaign = Campaign(
        campaign_id="CAMP-000001",
        trades=(
            _close_trade(
                "IBM",
                "100",
                datetime(2026, 1, 10, 10, 0),
            ),
        ),
        started_before_data=True,
    )

    book = LotBook()
    book.seed(historical_lot)

    # This is intentionally the interface we want to establish.
    assignments = book.resolve_boundary_campaign(campaign)

    assert assignments == {
        "DEC-IBM": "CAMP-000001",
    }

    assert book.lots(Instrument("IBM"))[0].campaign_id == "CAMP-000001"


def test_boundary_campaign_may_resolve_after_multiple_partial_closes() -> None:
    book = LotBook()

    book.seed(
        Lot(
            lot_id="DEC-LMT",
            instrument=Instrument("LMT-480P"),
            quantity=Decimal("-5"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("20"),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-000002",
        trades=(
            Trade(
                legs=(
                    Leg(
                        instrument=Instrument("LMT-480P"),
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            Execution(
                                quantity=Decimal("1"),
                                execution_price=Decimal("30"),
                                executed_at=datetime(2026, 1, 5, 10, 0),
                            ),
                        ),
                    ),
                )
            ),
            Trade(
                legs=(
                    Leg(
                        instrument=Instrument("LMT-480P"),
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            Execution(
                                quantity=Decimal("4"),
                                execution_price=Decimal("30"),
                                executed_at=datetime(2026, 1, 6, 10, 0),
                            ),
                        ),
                    ),
                )
            ),
        ),
        started_before_data=True,
    )

    assignments = book.resolve_boundary_campaign(campaign)

    assert assignments == {
        "DEC-LMT": "CAMP-000002",
    }

    assert book.lots(Instrument("LMT-480P"))[0].campaign_id == "CAMP-000002"


def test_boundary_campaign_does_not_claim_partially_consumed_lot() -> None:
    book = LotBook()

    book.seed(
        Lot(
            lot_id="DEC-IBM",
            instrument=Instrument("IBM"),
            quantity=Decimal("100"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("9000"),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-000003",
        trades=(
            _close_trade(
                "IBM",
                "40",
                datetime(2026, 1, 10, 10, 0),
            ),
        ),
        started_before_data=True,
    )

    assignments = book.resolve_boundary_campaign(campaign)

    assert assignments == {}
    assert book.lots(Instrument("IBM"))[0].campaign_id is None
    assert book.lots(Instrument("IBM"))[0].quantity == Decimal("100")


def test_non_boundary_campaign_does_not_claim_historical_lot() -> None:
    book = LotBook()

    book.seed(
        Lot(
            lot_id="DEC-IBM",
            instrument=Instrument("IBM"),
            quantity=Decimal("100"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("9000"),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-000004",
        trades=(
            Trade(
                legs=(
                    Leg(
                        instrument=Instrument("IBM"),
                        side=Side.BUY,
                        position_effect=PositionEffect.OPEN,
                        executions=(
                            Execution(
                                quantity=Decimal("100"),
                                execution_price=Decimal("95"),
                                executed_at=datetime(2026, 1, 10, 10, 0),
                            ),
                        ),
                    ),
                )
            ),
        ),
        started_before_data=False,
    )

    assignments = book.resolve_boundary_campaign(campaign)

    assert assignments == {}
    assert book.lots(Instrument("IBM"))[0].campaign_id is None

def test_mixed_open_and_close_campaign_can_claim_historical_close_lot() -> None:
    historical_lot = Lot(
        lot_id="DEC-COIN-220C",
        instrument=Instrument("COIN-220C"),
        quantity=Decimal("-5"),
        opened_at=datetime(2025, 12, 31, 16, 0),
        basis_total=Decimal("20000"),
        basis_source="DECEMBER_SNAPSHOT",
    )

    # The real-world pattern we're protecting:
    # one trade opens a new position while closing an older,
    # pre-period position in the same campaign.
    mixed_trade = Trade(
        legs=(
            Leg(
                instrument=Instrument("COIN-220C"),
                side=Side.BUY,
                position_effect=PositionEffect.CLOSE,
                executions=(
                    Execution(
                        quantity=Decimal("5"),
                        execution_price=Decimal("20"),
                        executed_at=datetime(2026, 1, 5, 10, 0),
                    ),
                ),
            ),
            Leg(
                instrument=Instrument("COIN-200C"),
                side=Side.SELL,
                position_effect=PositionEffect.OPEN,
                executions=(
                    Execution(
                        quantity=Decimal("5"),
                        execution_price=Decimal("30"),
                        executed_at=datetime(2026, 1, 5, 10, 0),
                    ),
                ),
            ),
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-000005",
        trades=(mixed_trade,),
        started_before_data=False,
    )

    book = LotBook()
    book.seed(historical_lot)

    assignments = book.resolve_boundary_campaign(campaign)

    assert assignments == {
        "DEC-COIN-220C": "CAMP-000005",
    }

    assert book.lots(Instrument("COIN-220C"))[0].campaign_id == "CAMP-000005"

def test_mixed_campaign_can_claim_historical_lots_consumed_by_later_trade() -> None:
    book = LotBook()

    book.seed(
        Lot(
            lot_id="DEC-COIN-220C",
            instrument=Instrument("COIN-220C"),
            quantity=Decimal("-5"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("20000"),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )

    book.seed(
        Lot(
            lot_id="DEC-COIN",
            instrument=Instrument("COIN"),
            quantity=Decimal("500"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("125000"),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-000006",
        trades=(
            Trade(
                legs=(
                    Leg(
                        instrument=Instrument("COIN-220C"),
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            Execution(
                                quantity=Decimal("5"),
                                execution_price=Decimal("20"),
                                executed_at=datetime(2026, 1, 5, 10, 0),
                            ),
                        ),
                    ),
                    Leg(
                        instrument=Instrument("COIN-200C"),
                        side=Side.SELL,
                        position_effect=PositionEffect.OPEN,
                        executions=(
                            Execution(
                                quantity=Decimal("5"),
                                execution_price=Decimal("30"),
                                executed_at=datetime(2026, 1, 5, 10, 0),
                            ),
                        ),
                    ),
                ),
            ),
            Trade(
                legs=(
                    Leg(
                        instrument=Instrument("COIN-200C"),
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            Execution(
                                quantity=Decimal("5"),
                                execution_price=Decimal("24"),
                                executed_at=datetime(2026, 1, 23, 13, 0),
                            ),
                        ),
                    ),
                    Leg(
                        instrument=Instrument("COIN"),
                        side=Side.SELL,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            Execution(
                                quantity=Decimal("500"),
                                execution_price=Decimal("217"),
                                executed_at=datetime(2026, 1, 23, 13, 0),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        started_before_data=False,
    )

    assignments = book.resolve_boundary_campaign(campaign)

    assert assignments == {
        "DEC-COIN-220C": "CAMP-000006",
        "DEC-COIN": "CAMP-000006",
    }

    assert (
        book.lots(Instrument("COIN-220C"))[0].campaign_id
        == "CAMP-000006"
    )
    assert (
        book.lots(Instrument("COIN"))[0].campaign_id
        == "CAMP-000006"
    )
