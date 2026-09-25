from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from campaigniq.analytics.realized_drawdown import (
    RealizedDrawdownSummary,
    RealizedEquityPoint,
    summarize_realized_drawdown,
)


def attribution(
    closed_date: date,
    gain_loss: str,
):
    """Minimal attribution-shaped object for analytics isolation."""

    record = SimpleNamespace(
        closed_date=closed_date,
        gain_loss=Decimal(gain_loss),
    )
    return SimpleNamespace(record=record)


def test_empty_drawdown_summary() -> None:
    result = summarize_realized_drawdown(())

    assert result == RealizedDrawdownSummary(
        points=(),
        total_realized_pnl=Decimal("0"),
        maximum_drawdown=Decimal("0"),
        maximum_drawdown_peak_date=None,
        maximum_drawdown_trough_date=None,
        recovery_date=None,
        current_drawdown=Decimal("0"),
        current_peak_date=None,
        recovered=True,
    )


def test_builds_chronological_realized_equity_curve() -> None:
    result = summarize_realized_drawdown(
        (
            attribution(date(2026, 1, 3), "50.00"),
            attribution(date(2026, 1, 1), "100.00"),
            attribution(date(2026, 1, 2), "-40.00"),
        )
    )

    assert result.points == (
        RealizedEquityPoint(
            closed_date=date(2026, 1, 1),
            realized_pnl=Decimal("100.00"),
            cumulative_pnl=Decimal("100.00"),
            running_peak_pnl=Decimal("100.00"),
            drawdown=Decimal("0.00"),
        ),
        RealizedEquityPoint(
            closed_date=date(2026, 1, 2),
            realized_pnl=Decimal("-40.00"),
            cumulative_pnl=Decimal("60.00"),
            running_peak_pnl=Decimal("100.00"),
            drawdown=Decimal("-40.00"),
        ),
        RealizedEquityPoint(
            closed_date=date(2026, 1, 3),
            realized_pnl=Decimal("50.00"),
            cumulative_pnl=Decimal("110.00"),
            running_peak_pnl=Decimal("110.00"),
            drawdown=Decimal("0.00"),
        ),
    )

    assert result.total_realized_pnl == Decimal("110.00")


def test_aggregates_multiple_records_on_same_close_date() -> None:
    result = summarize_realized_drawdown(
        (
            attribution(date(2026, 2, 1), "100.00"),
            attribution(date(2026, 2, 2), "-150.00"),
            attribution(date(2026, 2, 2), "40.00"),
        )
    )

    assert len(result.points) == 2

    assert result.points[1] == RealizedEquityPoint(
        closed_date=date(2026, 2, 2),
        realized_pnl=Decimal("-110.00"),
        cumulative_pnl=Decimal("-10.00"),
        running_peak_pnl=Decimal("100.00"),
        drawdown=Decimal("-110.00"),
    )


def test_initial_loss_draws_down_from_zero_high_water_mark() -> None:
    result = summarize_realized_drawdown(
        (
            attribution(date(2026, 3, 1), "-1000.00"),
            attribution(date(2026, 3, 2), "400.00"),
            attribution(date(2026, 3, 3), "1200.00"),
        )
    )

    assert [point.drawdown for point in result.points] == [
        Decimal("-1000.00"),
        Decimal("-600.00"),
        Decimal("0.00"),
    ]

    assert result.maximum_drawdown == Decimal("-1000.00")
    assert result.maximum_drawdown_peak_date is None
    assert result.maximum_drawdown_trough_date == date(2026, 3, 1)
    assert result.recovery_date == date(2026, 3, 3)
    assert result.recovered is True


def test_identifies_peak_trough_and_recovery() -> None:
    result = summarize_realized_drawdown(
        (
            attribution(date(2026, 4, 1), "500.00"),
            attribution(date(2026, 4, 2), "-200.00"),
            attribution(date(2026, 4, 3), "-400.00"),
            attribution(date(2026, 4, 4), "300.00"),
            attribution(date(2026, 4, 5), "400.00"),
        )
    )

    assert result.maximum_drawdown == Decimal("-600.00")
    assert result.maximum_drawdown_peak_date == date(2026, 4, 1)
    assert result.maximum_drawdown_trough_date == date(2026, 4, 3)
    assert result.recovery_date == date(2026, 4, 5)

    assert result.current_drawdown == Decimal("0.00")
    assert result.current_peak_date == date(2026, 4, 5)
    assert result.recovered is True


def test_reports_unrecovered_current_drawdown() -> None:
    result = summarize_realized_drawdown(
        (
            attribution(date(2026, 5, 1), "800.00"),
            attribution(date(2026, 5, 2), "-500.00"),
            attribution(date(2026, 5, 3), "100.00"),
        )
    )

    assert result.maximum_drawdown == Decimal("-500.00")
    assert result.maximum_drawdown_peak_date == date(2026, 5, 1)
    assert result.maximum_drawdown_trough_date == date(2026, 5, 2)
    assert result.recovery_date is None

    assert result.current_drawdown == Decimal("-400.00")
    assert result.current_peak_date == date(2026, 5, 1)
    assert result.recovered is False


def test_no_drawdown_when_curve_only_makes_new_highs() -> None:
    result = summarize_realized_drawdown(
        (
            attribution(date(2026, 6, 1), "100.00"),
            attribution(date(2026, 6, 2), "50.00"),
            attribution(date(2026, 6, 3), "25.00"),
        )
    )

    assert result.maximum_drawdown == Decimal("0")
    assert result.maximum_drawdown_peak_date is None
    assert result.maximum_drawdown_trough_date is None
    assert result.recovery_date is None
    assert result.current_drawdown == Decimal("0.00")
    assert result.current_peak_date == date(2026, 6, 3)
    assert result.recovered is True


def test_accepts_generator_and_is_input_order_independent() -> None:
    source = (
        (date(2026, 7, 3), "25.00"),
        (date(2026, 7, 1), "100.00"),
        (date(2026, 7, 2), "-30.00"),
        (date(2026, 7, 2), "-20.00"),
    )

    result = summarize_realized_drawdown(
        attribution(closed_date, pnl)
        for closed_date, pnl in source
    )

    assert [point.closed_date for point in result.points] == [
        date(2026, 7, 1),
        date(2026, 7, 2),
        date(2026, 7, 3),
    ]

    assert result.points[1].realized_pnl == Decimal("-50.00")
    assert result.maximum_drawdown == Decimal("-50.00")
    assert result.current_drawdown == Decimal("-25.00")
    assert result.total_realized_pnl == Decimal("75.00")
