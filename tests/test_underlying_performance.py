from datetime import date
from decimal import Decimal

from campaigniq.analytics.underlying_performance import (
    UnderlyingPerformance,
    summarize_underlying_performance,
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


def test_summarizes_repeated_campaigns_by_underlying() -> None:
    monthly = {
        (date(2026, 1, 1), date(2026, 1, 31)): (
            attr("CAMP-000001", Instrument("AAPL"), date(2026, 1, 5), "100"),
            attr("CAMP-000002", Instrument("AAPL"), date(2026, 1, 20), "-40"),
        ),
        (date(2026, 2, 1), date(2026, 2, 28)): (
            attr("CAMP-000001", Instrument("AAPL"), date(2026, 2, 5), "60"),
        ),
    }

    assert summarize_underlying_performance(monthly) == (
        UnderlyingPerformance(
            underlying="AAPL",
            realized_pnl=Decimal("120"),
            campaign_count=3,
            winning_campaign_count=2,
            losing_campaign_count=1,
            breakeven_campaign_count=0,
            win_rate=Decimal(2) / Decimal(3),
            average_campaign_pnl=Decimal("40"),
            median_campaign_pnl=Decimal("60"),
            best_campaign_id="2026-01/CAMP-000001",
            best_campaign_pnl=Decimal("100"),
            worst_campaign_id="2026-01/CAMP-000002",
            worst_campaign_pnl=Decimal("-40"),
        ),
    )


def test_option_contract_uses_its_underlying_symbol() -> None:
    option = OptionContract(
        underlying="MSFT",
        expiration=date(2026, 3, 20),
        strike=Decimal("400"),
        option_type=OptionType.CALL,
    )
    monthly = {
        (date(2026, 3, 1), date(2026, 3, 31)): (
            attr("CAMP-1", option, date(2026, 3, 10), "25"),
        ),
    }

    result = summarize_underlying_performance(monthly)
    assert result[0].underlying == "MSFT"
    assert result[0].realized_pnl == Decimal("25")


def test_multi_underlying_campaign_is_split_by_authoritative_records() -> None:
    monthly = {
        (date(2026, 4, 1), date(2026, 4, 30)): (
            attr("CAMP-1", Instrument("AAPL"), date(2026, 4, 5), "30"),
            attr("CAMP-1", Instrument("MSFT"), date(2026, 4, 6), "-10"),
        ),
    }

    result = summarize_underlying_performance(monthly)
    assert [(x.underlying, x.realized_pnl) for x in result] == [
        ("AAPL", Decimal("30")),
        ("MSFT", Decimal("-10")),
    ]
    assert all(x.campaign_count == 1 for x in result)


def test_excludes_unassigned_ambiguous_and_unreconciled_records() -> None:
    good = attr("GOOD", Instrument("AAPL"), date(2026, 5, 1), "10")
    unassigned = attr(None, Instrument("AAPL"), date(2026, 5, 2), "999")
    unreconciled = attr(
        "BAD",
        Instrument("AAPL"),
        date(2026, 5, 3),
        "999",
        allocation_basis="99",
    )
    ambiguous = RealizedAttribution(
        record=RealizedGainLossRecord(
            closed_date=date(2026, 5, 4),
            instrument=Instrument("AAPL"),
            quantity=Decimal("2"),
            closing_price=Decimal("1"),
            proceeds=Decimal("1200"),
            cost_basis=Decimal("200"),
            gain_loss=Decimal("1000"),
            basis_method="FIFO",
            term="SHORT",
        ),
        allocations=(
            LotAllocation("A", Decimal("1"), Decimal("100"), "TEST", "C1"),
            LotAllocation("B", Decimal("1"), Decimal("100"), "TEST", "C2"),
        ),
    )
    monthly = {
        (date(2026, 5, 1), date(2026, 5, 31)): (
            good,
            unassigned,
            unreconciled,
            ambiguous,
        ),
    }

    result = summarize_underlying_performance(monthly)
    assert len(result) == 1
    assert result[0].realized_pnl == Decimal("10")
    assert result[0].campaign_count == 1


def test_empty_input_returns_empty_tuple() -> None:
    assert summarize_underlying_performance({}) == ()
