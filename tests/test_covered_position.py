from datetime import date
from decimal import Decimal

import pytest

from campaigniq.domain.covered_position import (
    PositionQuantity,
    detect_covered_call_position,
)
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.value_objects.instrument import Instrument


def call(
    *,
    symbol: str = "NFLX",
    strike: str = "86",
) -> OptionContract:
    return OptionContract(
        underlying=symbol,
        expiration=date(2026, 2, 20),
        strike=Decimal(strike),
        option_type=OptionType.CALL,
    )


def put(
    *,
    symbol: str = "NFLX",
    strike: str = "86",
) -> OptionContract:
    return OptionContract(
        underlying=symbol,
        expiration=date(2026, 2, 20),
        strike=Decimal(strike),
        option_type=OptionType.PUT,
    )


def test_fifty_short_calls_are_covered_by_five_thousand_shares() -> None:
    result = detect_covered_call_position(
        [
            PositionQuantity(Instrument("NFLX"), Decimal("5000")),
            PositionQuantity(call(), Decimal("-50")),
        ],
        underlying="NFLX",
    )

    assert result.share_quantity == Decimal("5000")
    assert result.short_call_quantity == Decimal("50")
    assert result.required_share_quantity == Decimal("5000")
    assert result.covered_call_quantity == Decimal("50")
    assert result.uncovered_call_quantity == Decimal("0")
    assert result.excess_share_quantity == Decimal("0")
    assert result.fully_covered


def test_partial_coverage_is_reported_without_overclaiming() -> None:
    result = detect_covered_call_position(
        [
            PositionQuantity(Instrument("NFLX"), Decimal("3000")),
            PositionQuantity(call(), Decimal("-50")),
        ],
        underlying="NFLX",
    )

    assert result.covered_call_quantity == Decimal("30")
    assert result.uncovered_call_quantity == Decimal("20")
    assert not result.fully_covered


def test_excess_shares_are_reported() -> None:
    result = detect_covered_call_position(
        [
            PositionQuantity(Instrument("NFLX"), Decimal("5500")),
            PositionQuantity(call(), Decimal("-50")),
        ],
        underlying="NFLX",
    )

    assert result.covered_call_quantity == Decimal("50")
    assert result.uncovered_call_quantity == Decimal("0")
    assert result.excess_share_quantity == Decimal("500")
    assert result.fully_covered


def test_long_calls_do_not_create_short_call_coverage_requirement() -> None:
    result = detect_covered_call_position(
        [
            PositionQuantity(Instrument("NFLX"), Decimal("5000")),
            PositionQuantity(call(), Decimal("50")),
        ],
        underlying="NFLX",
    )

    assert result.short_call_quantity == Decimal("0")
    assert result.required_share_quantity == Decimal("0")
    assert not result.fully_covered


def test_short_puts_are_not_covered_calls() -> None:
    result = detect_covered_call_position(
        [
            PositionQuantity(Instrument("NFLX"), Decimal("5000")),
            PositionQuantity(put(), Decimal("-50")),
        ],
        underlying="NFLX",
    )

    assert result.short_call_quantity == Decimal("0")
    assert not result.fully_covered


def test_short_equity_does_not_cover_short_calls() -> None:
    result = detect_covered_call_position(
        [
            PositionQuantity(Instrument("NFLX"), Decimal("-5000")),
            PositionQuantity(call(), Decimal("-50")),
        ],
        underlying="NFLX",
    )

    assert result.share_quantity == Decimal("0")
    assert result.covered_call_quantity == Decimal("0")
    assert result.uncovered_call_quantity == Decimal("50")
    assert not result.fully_covered


def test_other_underlyings_are_ignored() -> None:
    result = detect_covered_call_position(
        [
            PositionQuantity(Instrument("AAPL"), Decimal("5000")),
            PositionQuantity(call(symbol="AAPL"), Decimal("-50")),
            PositionQuantity(Instrument("NFLX"), Decimal("100")),
            PositionQuantity(call(), Decimal("-1")),
        ],
        underlying="NFLX",
    )

    assert result.share_quantity == Decimal("100")
    assert result.short_call_quantity == Decimal("1")
    assert result.fully_covered


def test_multiple_short_calls_are_aggregated() -> None:
    result = detect_covered_call_position(
        [
            PositionQuantity(Instrument("NFLX"), Decimal("5000")),
            PositionQuantity(call(strike="86"), Decimal("-20")),
            PositionQuantity(call(strike="90"), Decimal("-30")),
        ],
        underlying="NFLX",
    )

    assert result.short_call_quantity == Decimal("50")
    assert result.required_share_quantity == Decimal("5000")
    assert result.fully_covered


def test_custom_contract_multiplier_is_supported() -> None:
    result = detect_covered_call_position(
        [
            PositionQuantity(Instrument("XYZ"), Decimal("500")),
            PositionQuantity(
                call(symbol="XYZ"),
                Decimal("-10"),
            ),
        ],
        underlying="XYZ",
        contract_multiplier=Decimal("50"),
    )

    assert result.required_share_quantity == Decimal("500")
    assert result.fully_covered


def test_empty_underlying_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        detect_covered_call_position([], underlying=" ")


def test_nonpositive_multiplier_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        detect_covered_call_position(
            [],
            underlying="NFLX",
            contract_multiplier=Decimal("0"),
        )


def test_no_position_is_not_fully_covered() -> None:
    result = detect_covered_call_position([], underlying="NFLX")

    assert result.share_quantity == 0
    assert result.short_call_quantity == 0
    assert result.covered_call_quantity == 0
    assert not result.fully_covered
