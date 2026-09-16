from decimal import Decimal

from campaigniq.analytics.campaign_outcome_distribution import (
    CampaignOutcomeDistribution,
    summarize_campaign_outcomes,
)
from campaigniq.domain.campaign_realized_pnl import CampaignRealizedPnl


def campaign(
    campaign_id: str,
    pnl: str,
    *,
    fully_reconciled: bool = True,
) -> CampaignRealizedPnl:
    return CampaignRealizedPnl(
        campaign_id=campaign_id,
        proceeds=Decimal("0"),
        cost_basis=Decimal("0"),
        gain_loss=Decimal(pnl),
        allocation_count=1,
        record_count=1,
        fully_reconciled=fully_reconciled,
    )


def test_summarizes_campaign_outcome_distribution() -> None:
    results = (
        campaign("A", "100.00"),
        campaign("B", "300.00"),
        campaign("C", "500.00"),
        campaign("D", "-50.00"),
        campaign("E", "-150.00"),
        campaign("F", "-1000.00"),
        campaign("G", "0.00"),
        campaign("EXCLUDED", "-99999.00", fully_reconciled=False),
    )

    summary = summarize_campaign_outcomes(results)

    assert summary == CampaignOutcomeDistribution(
        campaign_count=7,
        excluded_campaign_count=1,
        median_campaign_pnl=Decimal("0.00"),
        median_win=Decimal("300.00"),
        median_loss=Decimal("-150.00"),
        best_campaign_id="C",
        best_campaign_pnl=Decimal("500.00"),
        worst_campaign_id="F",
        worst_campaign_pnl=Decimal("-1000.00"),
        top_3_winner_pnl=Decimal("900.00"),
        bottom_3_loser_pnl=Decimal("-1200.00"),
    )


def test_top_and_bottom_three_use_available_campaigns() -> None:
    results = (
        campaign("WIN", "75.00"),
        campaign("LOSS", "-25.00"),
    )

    summary = summarize_campaign_outcomes(results)

    assert summary.top_3_winner_pnl == Decimal("75.00")
    assert summary.bottom_3_loser_pnl == Decimal("-25.00")


def test_empty_campaign_outcome_distribution() -> None:
    summary = summarize_campaign_outcomes(())

    assert summary == CampaignOutcomeDistribution(
        campaign_count=0,
        excluded_campaign_count=0,
        median_campaign_pnl=None,
        median_win=None,
        median_loss=None,
        best_campaign_id=None,
        best_campaign_pnl=None,
        worst_campaign_id=None,
        worst_campaign_pnl=None,
        top_3_winner_pnl=Decimal("0"),
        bottom_3_loser_pnl=Decimal("0"),
    )


def test_accepts_generator_input() -> None:
    results = (
        result
        for result in (
            campaign("A", "10.00"),
            campaign("B", "-20.00"),
            campaign("C", "30.00"),
        )
    )

    summary = summarize_campaign_outcomes(results)

    assert summary.campaign_count == 3
    assert summary.median_campaign_pnl == Decimal("10.00")
    assert summary.best_campaign_id == "C"
    assert summary.worst_campaign_id == "B"
