from datetime import date
from decimal import Decimal

from campaigniq.analytics.repeated_campaign_performance import (
    RepeatedCampaignPerformance,
    summarize_repeated_campaign_performance,
)
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument


def attr(
    campaign_id: str | None,
    instrument,
    day: date,
    pnl: str,
    *,
    basis: str = "100",
    allocation_basis: str | None = None,
) -> RealizedAttribution:
    gain = Decimal(pnl)
    broker_basis = Decimal(basis)
    alloc_basis = broker_basis if allocation_basis is None else Decimal(allocation_basis)
    return RealizedAttribution(
        record=RealizedGainLossRecord(
            closed_date=day,
            instrument=instrument,
            quantity=Decimal("1"),
            closing_price=Decimal("1"),
            proceeds=broker_basis + gain,
            cost_basis=broker_basis,
            gain_loss=gain,
            basis_method="FIFO",
            term="SHORT",
        ),
        allocations=(
            LotAllocation(
                lot_id=f"{day}-{campaign_id}",
                quantity=Decimal("1"),
                broker_basis=alloc_basis,
                basis_source="TEST",
                campaign_id=campaign_id,
            ),
        ),
    )


def test_compares_first_and_subsequent_realized_campaigns() -> None:
    monthly = {
        (date(2026, 1, 1), date(2026, 1, 31)): (
            attr("C2", Instrument("AAPL"), date(2026, 1, 20), "-40"),
            attr("C1", Instrument("AAPL"), date(2026, 1, 5), "100"),
        ),
        (date(2026, 2, 1), date(2026, 2, 28)): (
            attr("C1", Instrument("AAPL"), date(2026, 2, 5), "60"),
        ),
    }

    assert summarize_repeated_campaign_performance(monthly) == (
        RepeatedCampaignPerformance(
            underlying="AAPL",
            campaign_count=3,
            first_campaign_id="2026-01/C1",
            first_realized_date=date(2026, 1, 5),
            first_campaign_pnl=Decimal("100"),
            subsequent_campaign_count=2,
            subsequent_realized_pnl=Decimal("20"),
            subsequent_winning_campaign_count=1,
            subsequent_losing_campaign_count=1,
            subsequent_breakeven_campaign_count=0,
            subsequent_win_rate=Decimal("0.5"),
            subsequent_average_campaign_pnl=Decimal("10"),
            subsequent_median_campaign_pnl=Decimal("10"),
        ),
    )


def test_aggregates_multiple_records_before_ordering_campaigns() -> None:
    monthly = {
        (date(2026, 3, 1), date(2026, 3, 31)): (
            attr("LATE-ID", Instrument("MSFT"), date(2026, 3, 4), "10"),
            attr("LATE-ID", Instrument("MSFT"), date(2026, 3, 8), "20"),
            attr("EARLY-ID", Instrument("MSFT"), date(2026, 3, 2), "5"),
        ),
    }
    result = summarize_repeated_campaign_performance(monthly)[0]
    assert result.first_campaign_id == "2026-03/EARLY-ID"
    assert result.first_campaign_pnl == Decimal("5")
    assert result.subsequent_realized_pnl == Decimal("30")


def test_same_date_uses_period_qualified_campaign_id_tie_breaker() -> None:
    monthly = {
        (date(2026, 4, 1), date(2026, 4, 30)): (
            attr("B", Instrument("AAPL"), date(2026, 4, 5), "20"),
            attr("A", Instrument("AAPL"), date(2026, 4, 5), "10"),
        ),
    }
    result = summarize_repeated_campaign_performance(monthly)[0]
    assert result.first_campaign_id == "2026-04/A"
    assert result.first_campaign_pnl == Decimal("10")


def test_omits_underlying_with_only_one_campaign() -> None:
    monthly = {
        (date(2026, 5, 1), date(2026, 5, 31)): (
            attr("ONLY", Instrument("AAPL"), date(2026, 5, 1), "10"),
        ),
    }
    assert summarize_repeated_campaign_performance(monthly) == ()


def test_option_contract_uses_underlying_symbol() -> None:
    option = OptionContract(
        underlying="MSFT",
        expiration=date(2026, 6, 19),
        strike=Decimal("400"),
        option_type=OptionType.CALL,
    )
    monthly = {
        (date(2026, 6, 1), date(2026, 6, 30)): (
            attr("C1", option, date(2026, 6, 2), "10"),
            attr("C2", option, date(2026, 6, 20), "20"),
        ),
    }
    result = summarize_repeated_campaign_performance(monthly)
    assert result[0].underlying == "MSFT"


def test_excludes_unassigned_and_unreconciled_records() -> None:
    good1 = attr("GOOD1", Instrument("AAPL"), date(2026, 7, 1), "10")
    good2 = attr("GOOD2", Instrument("AAPL"), date(2026, 7, 2), "20")
    unassigned = attr(None, Instrument("AAPL"), date(2026, 7, 3), "999")
    unreconciled = attr(
        "BAD",
        Instrument("AAPL"),
        date(2026, 7, 4),
        "999",
        allocation_basis="99",
    )
    result = summarize_repeated_campaign_performance(
        {(date(2026, 7, 1), date(2026, 7, 31)): (good1, good2, unassigned, unreconciled)}
    )
    assert len(result) == 1
    assert result[0].campaign_count == 2
    assert result[0].subsequent_realized_pnl == Decimal("20")


def test_empty_input_returns_empty_tuple() -> None:
    assert summarize_repeated_campaign_performance({}) == ()
