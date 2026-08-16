from datetime import date
from decimal import Decimal

from campaigniq.domain.campaign_realized_pnl import (
    CampaignRealizedPnl,
    aggregate_campaign_realized_pnl,
)
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument


def record(
    *,
    quantity: str,
    proceeds: str,
    cost_basis: str,
    gain_loss: str,
) -> RealizedGainLossRecord:
    return RealizedGainLossRecord(
        closed_date=date(2026, 1, 10),
        instrument=Instrument("IBM"),
        quantity=Decimal(quantity),
        closing_price=Decimal("210"),
        proceeds=Decimal(proceeds),
        cost_basis=Decimal(cost_basis),
        gain_loss=Decimal(gain_loss),
        basis_method="FIFO",
        term="SHORT TERM",
    )


def allocation(
    *,
    lot_id: str,
    quantity: str,
    basis: str,
    campaign_id: str | None,
) -> LotAllocation:
    return LotAllocation(
        lot_id=lot_id,
        quantity=Decimal(quantity),
        broker_basis=Decimal(basis),
        basis_source="SCHWAB_REALIZED_GAIN_LOSS",
        campaign_id=campaign_id,
    )


def test_aggregates_multiple_realized_records_for_one_campaign() -> None:
    attributions = [
        RealizedAttribution(
            record=record(
                quantity="100",
                proceeds="21000",
                cost_basis="20000",
                gain_loss="1000",
            ),
            allocations=(
                allocation(
                    lot_id="LOT-1",
                    quantity="100",
                    basis="20000",
                    campaign_id="CAMP-000001",
                ),
            ),
        ),
        RealizedAttribution(
            record=record(
                quantity="50",
                proceeds="11000",
                cost_basis="9000",
                gain_loss="2000",
            ),
            allocations=(
                allocation(
                    lot_id="LOT-2",
                    quantity="50",
                    basis="9000",
                    campaign_id="CAMP-000001",
                ),
            ),
        ),
    ]

    assert aggregate_campaign_realized_pnl(attributions) == (
        CampaignRealizedPnl(
            campaign_id="CAMP-000001",
            proceeds=Decimal("32000"),
            cost_basis=Decimal("29000"),
            gain_loss=Decimal("3000"),
            allocation_count=2,
            record_count=2,
            fully_reconciled=True,
        ),
    )


def test_does_not_silently_split_record_across_campaigns() -> None:
    attribution = RealizedAttribution(
        record=record(
            quantity="100",
            proceeds="21000",
            cost_basis="20000",
            gain_loss="1000",
        ),
        allocations=(
            allocation(
                lot_id="LOT-1",
                quantity="50",
                basis="10000",
                campaign_id="CAMP-000001",
            ),
            allocation(
                lot_id="LOT-2",
                quantity="50",
                basis="10000",
                campaign_id="CAMP-000002",
            ),
        ),
    )

    assert aggregate_campaign_realized_pnl([attribution]) == ()


def test_does_not_aggregate_unknown_campaign_provenance() -> None:
    attribution = RealizedAttribution(
        record=record(
            quantity="100",
            proceeds="21000",
            cost_basis="20000",
            gain_loss="1000",
        ),
        allocations=(
            allocation(
                lot_id="LOT-1",
                quantity="100",
                basis="20000",
                campaign_id=None,
            ),
        ),
    )

    assert aggregate_campaign_realized_pnl([attribution]) == ()


def test_marks_campaign_not_fully_reconciled_when_broker_values_disagree() -> None:
    attribution = RealizedAttribution(
        record=record(
            quantity="100",
            proceeds="21000",
            cost_basis="20000",
            gain_loss="999",
        ),
        allocations=(
            allocation(
                lot_id="LOT-1",
                quantity="100",
                basis="20000",
                campaign_id="CAMP-000001",
            ),
        ),
    )

    result = aggregate_campaign_realized_pnl([attribution])[0]

    assert result.gain_loss == Decimal("999")
    assert result.fully_reconciled is False
