from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.boundary_reconstruction import (
    BoundaryReconstructionAnalyzer,
)
from campaigniq.domain.campaign import Campaign
from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument


def _close_trade(symbol: str, quantity: str) -> Trade:
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
                        executed_at=datetime(2026, 1, 10, 10, 0),
                    ),
                ),
            ),
        )
    )


def test_actual_boundary_case_is_derived_from_campaigns() -> None:
    campaigns = [
        Campaign(
            campaign_id="CAMP-000009",
            trades=(_close_trade("AMZN", "5"),),
            started_before_data=True,
        ),
        Campaign(
            campaign_id="CAMP-000010",
            trades=(_close_trade("COIN", "5"),),
            started_before_data=False,
        ),
    ]

    result = BoundaryReconstructionAnalyzer().analyze(
        period_start=date(2026, 1, 1),
        campaigns=campaigns,
        opening_lot_book=LotBook(),
    )

    assert result.unresolved_campaigns == ("CAMP-000009",)
    assert result.unresolved_positions == (
        "Instrument(symbol='AMZN')",
    )
    assert result.historical_requirements[0].months == ("2025-12",)
    assert result.historical_requirements[0].document_types == (
        "Account Trade History",
        "Brokerage Statement",
    )


def test_supplied_opening_lot_resolves_campaign_and_removes_requirement() -> None:
    book = LotBook()
    book.seed(
        Lot(
            lot_id="DEC-AMZN",
            instrument=Instrument("AMZN"),
            quantity=Decimal("5"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("9000"),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-000009",
        trades=(_close_trade("AMZN", "5"),),
        started_before_data=True,
    )

    result = BoundaryReconstructionAnalyzer().analyze(
        period_start=date(2026, 1, 1),
        campaigns=[campaign],
        opening_lot_book=book,
    )

    assert result.unresolved_campaigns == ()
    assert result.unresolved_positions == ()
    assert result.historical_requirements == ()
    assert book.lots(Instrument("AMZN"))[0].campaign_id == "CAMP-000009"


def test_multiple_unresolved_campaigns_share_position_requirements_without_merging_cases() -> None:
    campaigns = [
        Campaign(
            campaign_id="CAMP-000009",
            trades=(_close_trade("AMZN", "5"),),
            started_before_data=True,
        ),
        Campaign(
            campaign_id="CAMP-000012",
            trades=(_close_trade("LMT", "5"),),
            started_before_data=True,
        ),
    ]

    result = BoundaryReconstructionAnalyzer().analyze(
        period_start=date(2026, 1, 1),
        campaigns=campaigns,
        opening_lot_book=LotBook(),
    )

    assert result.unresolved_campaigns == (
        "CAMP-000009",
        "CAMP-000012",
    )
    assert result.unresolved_positions == (
        "Instrument(symbol='AMZN')",
        "Instrument(symbol='LMT')",
    )
    assert tuple(req.case_id for req in result.historical_requirements) == (
        "CAMP-000009",
        "CAMP-000012",
    )
