from datetime import date

import pytest

from campaigniq.domain.boundary_validation import (
    BoundaryStatus,
    BoundaryValidator,
    HistoricalRequirement,
    PendingActivityStatus,
    month_labels,
)


def test_complete_first_month_onboarding_is_complete() -> None:
    result = BoundaryValidator().validate(
        period="2026-01",
        opening_inventory=BoundaryStatus.COMPLETE,
        opening_inventory_source="December 2025 Brokerage Statement",
        trading_activity=BoundaryStatus.COMPLETE,
        ending_inventory=BoundaryStatus.COMPLETE,
        ending_inventory_source="January 2026 Brokerage Statement",
        realized_pnl=BoundaryStatus.COMPLETE,
    )

    assert result.complete is True
    assert result.has_action_required is False


def test_missing_immediate_prior_month_requires_actionable_history() -> None:
    requirement = HistoricalRequirement(
        case_id="COIN",
        earliest_unresolved_date=date(2025, 12, 1),
        months=("2025-12",),
        document_types=("Account Trade History", "Brokerage Statement"),
        reason="Opening COIN position cannot be established from supplied history.",
    )

    result = BoundaryValidator().validate(
        period="2026-01",
        opening_inventory=BoundaryStatus.PARTIAL,
        opening_inventory_source=None,
        trading_activity=BoundaryStatus.COMPLETE,
        ending_inventory=BoundaryStatus.COMPLETE,
        ending_inventory_source="January 2026 Brokerage Statement",
        realized_pnl=BoundaryStatus.COMPLETE,
        unresolved_positions=("COIN",),
        unresolved_campaigns=("CAMP-000001",),
        historical_requirements=(requirement,),
    )

    assert result.complete is False
    assert result.has_action_required is True
    assert result.historical_requirements[0].months == ("2025-12",)
    assert result.historical_requirements[0].document_types == (
        "Account Trade History",
        "Brokerage Statement",
    )


def test_campaign_requiring_multiple_months_requests_the_whole_needed_range() -> None:
    requirement = HistoricalRequirement(
        case_id="LIN",
        earliest_unresolved_date=date(2025, 11, 15),
        months=month_labels(date(2025, 11, 15), date(2025, 12, 31)),
        document_types=("Account Trade History", "Brokerage Statement"),
        reason="Campaign ancestry remains unresolved before the available data window.",
    )

    assert requirement.months == ("2025-11", "2025-12")

    result = BoundaryValidator().validate(
        period="2026-01",
        opening_inventory=BoundaryStatus.PARTIAL,
        opening_inventory_source="January 2026 Brokerage Statement",
        trading_activity=BoundaryStatus.COMPLETE,
        ending_inventory=BoundaryStatus.COMPLETE,
        ending_inventory_source="January 2026 Brokerage Statement",
        realized_pnl=BoundaryStatus.COMPLETE,
        unresolved_campaigns=("LIN",),
        historical_requirements=(requirement,),
    )

    assert result.historical_requirements[0].months == (
        "2025-11",
        "2025-12",
    )


def test_user_exclusion_is_explicit_and_not_resolved() -> None:
    result = BoundaryValidator().validate(
        period="2026-01",
        opening_inventory=BoundaryStatus.PARTIAL,
        opening_inventory_source=None,
        trading_activity=BoundaryStatus.COMPLETE,
        ending_inventory=BoundaryStatus.COMPLETE,
        ending_inventory_source="January 2026 Brokerage Statement",
        realized_pnl=BoundaryStatus.COMPLETE,
        excluded_cases=("NFLX", "COIN", "LIN"),
    )

    assert result.complete is False
    assert result.excluded_cases == ("NFLX", "COIN", "LIN")
    assert any("3 case(s)" in warning for warning in result.warnings)


def test_restored_history_can_be_reported_as_complete() -> None:
    incomplete = BoundaryValidator().validate(
        period="2026-01",
        opening_inventory=BoundaryStatus.PARTIAL,
        opening_inventory_source=None,
        trading_activity=BoundaryStatus.COMPLETE,
        ending_inventory=BoundaryStatus.COMPLETE,
        ending_inventory_source="January 2026 Brokerage Statement",
        realized_pnl=BoundaryStatus.COMPLETE,
        unresolved_campaigns=("COIN",),
    )
    assert incomplete.complete is False

    restored = BoundaryValidator().validate(
        period="2026-01",
        opening_inventory=BoundaryStatus.COMPLETE,
        opening_inventory_source="December 2025 Brokerage Statement",
        trading_activity=BoundaryStatus.COMPLETE,
        ending_inventory=BoundaryStatus.COMPLETE,
        ending_inventory_source="January 2026 Brokerage Statement",
        realized_pnl=BoundaryStatus.COMPLETE,
    )
    assert restored.complete is True


def test_pending_settlement_is_boundary_notice_not_missing_data() -> None:
    result = BoundaryValidator().validate(
        period="2026-01",
        opening_inventory=BoundaryStatus.COMPLETE,
        opening_inventory_source="December 2025 Brokerage Statement",
        trading_activity=BoundaryStatus.COMPLETE,
        ending_inventory=BoundaryStatus.COMPLETE,
        ending_inventory_source="January 2026 Brokerage Statement",
        realized_pnl=BoundaryStatus.COMPLETE,
        pending_activity=PendingActivityStatus.PRESENT,
    )

    assert result.complete is True
    assert result.pending_activity is PendingActivityStatus.PRESENT
    assert any("legitimate period-boundary" in warning for warning in result.warnings)


def test_month_labels_rejects_reversed_range() -> None:
    with pytest.raises(ValueError):
        month_labels(date(2026, 1, 1), date(2025, 12, 31))


def test_partial_realized_pnl_prevents_complete_status() -> None:
    result = BoundaryValidator().validate(
        period="2026-01",
        opening_inventory=BoundaryStatus.COMPLETE,
        opening_inventory_source="December 2025 Brokerage Statement",
        trading_activity=BoundaryStatus.COMPLETE,
        ending_inventory=BoundaryStatus.COMPLETE,
        ending_inventory_source="January 2026 Brokerage Statement",
        realized_pnl=BoundaryStatus.PARTIAL,
    )

    assert result.complete is False
