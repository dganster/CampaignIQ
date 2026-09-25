from datetime import date
from decimal import Decimal

from campaigniq.analytics.drawdown_contribution import (
    DrawdownUnderlyingContribution,
    summarize_maximum_drawdown_contributions,
)
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument


def attr(
    symbol: str,
    day: date,
    pnl: str,
    *,
    campaign_id: str | None = "CAMP-1",
    basis: str = "100",
    allocation_basis: str | None = None,
) -> RealizedAttribution:
    gain = Decimal(pnl)
    broker_basis = Decimal(basis)
    alloc_basis = (
        broker_basis
        if allocation_basis is None
        else Decimal(allocation_basis)
    )

    return RealizedAttribution(
        record=RealizedGainLossRecord(
            closed_date=day,
            instrument=Instrument(symbol),
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
                lot_id=f"{day}-{symbol}-{campaign_id}",
                quantity=Decimal("1"),
                broker_basis=alloc_basis,
                basis_source="TEST",
                campaign_id=campaign_id,
            ),
        ),
    )


def test_decomposes_maximum_drawdown_by_underlying() -> None:
    monthly = {
        (date(2026, 1, 1), date(2026, 1, 31)): (
            attr("AAPL", date(2026, 1, 5), "100"),
            attr("AAPL", date(2026, 1, 10), "-20"),
            attr("MSFT", date(2026, 1, 10), "-30"),
            attr("MSFT", date(2026, 1, 11), "10"),
            attr("AAPL", date(2026, 1, 12), "-40"),
        ),
    }

    result = summarize_maximum_drawdown_contributions(monthly)

    assert result.peak_date == date(2026, 1, 5)
    assert result.trough_date == date(2026, 1, 12)
    assert result.maximum_drawdown == Decimal("-80")
    assert result.interval_realized_pnl == Decimal("-80")
    assert result.record_count == 4

    assert result.contributions == (
        DrawdownUnderlyingContribution(
            underlying="AAPL",
            record_count=2,
            winning_record_count=0,
            losing_record_count=2,
            breakeven_record_count=0,
            gross_gain=Decimal("0"),
            gross_loss=Decimal("-60"),
            net_realized_pnl=Decimal("-60"),
            largest_gain=None,
            largest_loss=Decimal("-40"),
            drawdown_contribution=Decimal("-0.75"),
        ),
        DrawdownUnderlyingContribution(
            underlying="MSFT",
            record_count=2,
            winning_record_count=1,
            losing_record_count=1,
            breakeven_record_count=0,
            gross_gain=Decimal("10"),
            gross_loss=Decimal("-30"),
            net_realized_pnl=Decimal("-20"),
            largest_gain=Decimal("10"),
            largest_loss=Decimal("-30"),
            drawdown_contribution=Decimal("-0.25"),
        ),
    )


def test_positive_underlying_is_reported_as_drawdown_offset() -> None:
    monthly = {
        (date(2026, 1, 1), date(2026, 1, 31)): (
            attr("BASE", date(2026, 1, 1), "100"),
            attr("LOSS", date(2026, 1, 2), "-100"),
            attr("OFFSET", date(2026, 1, 2), "25"),
        ),
    }

    result = summarize_maximum_drawdown_contributions(monthly)

    by_underlying = {
        item.underlying: item
        for item in result.contributions
    }

    assert result.maximum_drawdown == Decimal("-75")
    assert by_underlying["LOSS"].drawdown_contribution == (
        Decimal("-100") / Decimal("75")
    )
    assert by_underlying["OFFSET"].drawdown_contribution == (
        Decimal("25") / Decimal("75")
    )


def test_period_qualification_excludes_adjacent_month_record() -> None:
    monthly = {
        (date(2026, 8, 1), date(2026, 8, 31)): (
            attr("AAPL", date(2026, 8, 1), "100"),
            attr("AAPL", date(2026, 8, 10), "-40"),
            attr("AAPL", date(2026, 9, 1), "-999"),
        ),
    }

    result = summarize_maximum_drawdown_contributions(monthly)

    assert result.maximum_drawdown == Decimal("-40")
    assert result.interval_realized_pnl == Decimal("-40")
    assert result.record_count == 1


def test_unassigned_and_unreconciled_records_remain_in_drawdown() -> None:
    monthly = {
        (date(2026, 5, 1), date(2026, 5, 31)): (
            attr("AAPL", date(2026, 5, 1), "100"),
            attr(
                "AAPL",
                date(2026, 5, 2),
                "-30",
                campaign_id=None,
            ),
            attr(
                "MSFT",
                date(2026, 5, 3),
                "-20",
                allocation_basis="99",
            ),
        ),
    }

    result = summarize_maximum_drawdown_contributions(monthly)

    assert result.maximum_drawdown == Decimal("-50")
    assert result.interval_realized_pnl == Decimal("-50")
    assert result.record_count == 2
    assert {
        item.underlying: item.net_realized_pnl
        for item in result.contributions
    } == {
        "AAPL": Decimal("-30"),
        "MSFT": Decimal("-20"),
    }


def test_initial_loss_uses_starting_zero_baseline() -> None:
    monthly = {
        (date(2026, 1, 1), date(2026, 1, 31)): (
            attr("AAPL", date(2026, 1, 5), "-25"),
            attr("MSFT", date(2026, 1, 6), "-15"),
        ),
    }

    result = summarize_maximum_drawdown_contributions(monthly)

    assert result.peak_date is None
    assert result.trough_date == date(2026, 1, 6)
    assert result.maximum_drawdown == Decimal("-40")
    assert result.interval_realized_pnl == Decimal("-40")
    assert result.record_count == 2


def test_no_drawdown_returns_empty_contributions() -> None:
    monthly = {
        (date(2026, 1, 1), date(2026, 1, 31)): (
            attr("AAPL", date(2026, 1, 5), "25"),
            attr("MSFT", date(2026, 1, 6), "10"),
        ),
    }

    result = summarize_maximum_drawdown_contributions(monthly)

    assert result.maximum_drawdown == Decimal("0")
    assert result.interval_realized_pnl == Decimal("0")
    assert result.record_count == 0
    assert result.contributions == ()


def test_empty_input_returns_empty_contributions() -> None:
    result = summarize_maximum_drawdown_contributions({})

    assert result.maximum_drawdown == Decimal("0")
    assert result.interval_realized_pnl == Decimal("0")
    assert result.record_count == 0
    assert result.contributions == ()
