from datetime import date
from decimal import Decimal

from campaigniq.analytics.multi_month_analytics_summary import (
    summarize_multi_month_analytics,
)
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.realized_attribution_store import (
    save_realized_attributions,
)
from campaigniq.persistence.persisted_multi_month_analytics import (
    load_persisted_monthly_attributions,
)


def attribution(
    *,
    closed_date: date,
    gain_loss: str,
    campaign_id: str,
) -> RealizedAttribution:
    gain_loss_decimal = Decimal(gain_loss)

    record = RealizedGainLossRecord(
        closed_date=closed_date,
        instrument=Instrument("IBM"),
        quantity=Decimal("100"),
        closing_price=Decimal("200"),
        proceeds=Decimal("20000"),
        cost_basis=Decimal("20000") - gain_loss_decimal,
        gain_loss=gain_loss_decimal,
        basis_method="FIFO",
        term="ST",
    )

    return RealizedAttribution(
        record=record,
        allocations=(
            LotAllocation(
                lot_id=f"LOT-{campaign_id}",
                quantity=record.quantity,
                broker_basis=record.cost_basis,
                basis_source="SCHWAB_REALIZED_GAIN_LOSS",
                campaign_id=campaign_id,
            ),
        ),
    )


def test_loads_persisted_periods_for_multi_month_analytics(tmp_path) -> None:
    january_path = tmp_path / "january.json"
    february_path = tmp_path / "february.json"

    save_realized_attributions(
        february_path,
        period_start=date(2026, 2, 1),
        period_end=date(2026, 2, 28),
        attributions=(
            attribution(
                closed_date=date(2026, 2, 10),
                gain_loss="-50.00",
                campaign_id="FEB-LOSS",
            ),
        ),
    )

    save_realized_attributions(
        january_path,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        attributions=(
            attribution(
                closed_date=date(2026, 1, 10),
                gain_loss="100.00",
                campaign_id="JAN-WIN",
            ),
        ),
    )

    monthly_attributions = load_persisted_monthly_attributions(
        [february_path, january_path]
    )

    summaries = summarize_multi_month_analytics(
        monthly_attributions=monthly_attributions
    )

    assert tuple(monthly_attributions) == (
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
    )

    january, february = summaries

    assert january.realized_pnl.broker_realized_pnl == Decimal("100.00")
    assert january.campaign_performance.campaign_count == 1
    assert january.campaign_performance.winning_campaign_count == 1

    assert february.realized_pnl.broker_realized_pnl == Decimal("-50.00")
    assert february.campaign_performance.campaign_count == 1
    assert february.campaign_performance.losing_campaign_count == 1


def test_rejects_duplicate_persisted_periods(tmp_path) -> None:
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"

    for path in (first_path, second_path):
        save_realized_attributions(
            path,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            attributions=(),
        )

    try:
        load_persisted_monthly_attributions(
            [first_path, second_path]
        )
    except ValueError as exc:
        assert "Duplicate persisted realized attribution period" in str(exc)
    else:
        raise AssertionError(
            "Expected duplicate persisted period to be rejected."
        )
