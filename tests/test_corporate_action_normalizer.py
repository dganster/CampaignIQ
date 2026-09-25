from datetime import date
from decimal import Decimal

import pytest

from campaigniq.domain.corporate_action import (
    CorporateActionEvidence,
    CorporateActionType,
)
from campaigniq.domain.corporate_action_normalizer import (
    CorporateActionNormalizer,
)


NFLX_SPLIT = CorporateActionEvidence(
    symbol="NFLX",
    effective_date=date(2025, 11, 17),
    action_type=CorporateActionType.FORWARD_SPLIT,
    new_units=Decimal("10"),
    old_units=Decimal("1"),
    source="SCHWAB",
    source_reference="Options Frwd Split",
)


def test_nflx_quantity_normalizes_forward_across_split() -> None:
    normalizer = CorporateActionNormalizer([NFLX_SPLIT])

    assert (
        normalizer.normalize_quantity(
            Decimal("5"),
            symbol="NFLX",
            source_date=date(2025, 11, 14),
            target_date=date(2025, 11, 18),
        )
        == Decimal("50")
    )


def test_nflx_quantity_normalizes_backward_across_split() -> None:
    normalizer = CorporateActionNormalizer([NFLX_SPLIT])

    assert (
        normalizer.normalize_quantity(
            Decimal("50"),
            symbol="NFLX",
            source_date=date(2025, 11, 18),
            target_date=date(2025, 11, 14),
        )
        == Decimal("5.0")
    )


def test_nflx_strike_normalizes_forward_across_split() -> None:
    normalizer = CorporateActionNormalizer([NFLX_SPLIT])

    assert (
        normalizer.normalize_price(
            Decimal("1140"),
            symbol="NFLX",
            source_date=date(2025, 11, 14),
            target_date=date(2025, 11, 18),
        )
        == Decimal("114.0")
    )


def test_nflx_strike_normalizes_backward_across_split() -> None:
    normalizer = CorporateActionNormalizer([NFLX_SPLIT])

    assert (
        normalizer.normalize_price(
            Decimal("114"),
            symbol="NFLX",
            source_date=date(2025, 11, 18),
            target_date=date(2025, 11, 14),
        )
        == Decimal("1140")
    )


def test_action_effective_date_belongs_to_post_action_unit_system() -> None:
    normalizer = CorporateActionNormalizer([NFLX_SPLIT])

    assert (
        normalizer.normalize_quantity(
            Decimal("5"),
            symbol="NFLX",
            source_date=date(2025, 11, 16),
            target_date=date(2025, 11, 17),
        )
        == Decimal("50")
    )

    assert (
        normalizer.normalize_quantity(
            Decimal("50"),
            symbol="NFLX",
            source_date=date(2025, 11, 17),
            target_date=date(2025, 11, 18),
        )
        == Decimal("50")
    )


def test_same_date_does_not_apply_action() -> None:
    normalizer = CorporateActionNormalizer([NFLX_SPLIT])

    assert (
        normalizer.normalize_quantity(
            Decimal("50"),
            symbol="NFLX",
            source_date=date(2025, 11, 17),
            target_date=date(2025, 11, 17),
        )
        == Decimal("50")
    )


def test_unrelated_symbol_is_not_normalized() -> None:
    normalizer = CorporateActionNormalizer([NFLX_SPLIT])

    assert (
        normalizer.normalize_quantity(
            Decimal("5"),
            symbol="AAPL",
            source_date=date(2025, 11, 1),
            target_date=date(2025, 12, 1),
        )
        == Decimal("5")
    )


def test_symbol_matching_is_case_insensitive() -> None:
    normalizer = CorporateActionNormalizer([NFLX_SPLIT])

    assert (
        normalizer.normalize_quantity(
            Decimal("5"),
            symbol="nflx",
            source_date=date(2025, 11, 14),
            target_date=date(2025, 11, 18),
        )
        == Decimal("50")
    )


def test_reverse_split_uses_same_general_arithmetic() -> None:
    reverse_split = CorporateActionEvidence(
        symbol="XYZ",
        effective_date=date(2026, 2, 1),
        action_type=CorporateActionType.REVERSE_SPLIT,
        new_units=Decimal("1"),
        old_units=Decimal("5"),
        source="BROKER",
    )
    normalizer = CorporateActionNormalizer([reverse_split])

    assert (
        normalizer.normalize_quantity(
            Decimal("100"),
            symbol="XYZ",
            source_date=date(2026, 1, 31),
            target_date=date(2026, 2, 1),
        )
        == Decimal("20.0")
    )
    assert (
        normalizer.normalize_price(
            Decimal("10"),
            symbol="XYZ",
            source_date=date(2026, 1, 31),
            target_date=date(2026, 2, 1),
        )
        == Decimal("50")
    )


def test_multiple_actions_compose_forward_and_backward() -> None:
    actions = [
        CorporateActionEvidence(
            symbol="XYZ",
            effective_date=date(2025, 6, 1),
            action_type=CorporateActionType.FORWARD_SPLIT,
            new_units=Decimal("2"),
            old_units=Decimal("1"),
            source="BROKER",
        ),
        CorporateActionEvidence(
            symbol="XYZ",
            effective_date=date(2026, 6, 1),
            action_type=CorporateActionType.REVERSE_SPLIT,
            new_units=Decimal("1"),
            old_units=Decimal("5"),
            source="BROKER",
        ),
    ]
    normalizer = CorporateActionNormalizer(reversed(actions))

    # 100 shares -> 200 after 2:1 -> 40 after 1:5.
    assert (
        normalizer.normalize_quantity(
            Decimal("100"),
            symbol="XYZ",
            source_date=date(2025, 1, 1),
            target_date=date(2026, 12, 1),
        )
        == Decimal("40.0")
    )

    assert (
        normalizer.normalize_quantity(
            Decimal("40"),
            symbol="XYZ",
            source_date=date(2026, 12, 1),
            target_date=date(2025, 1, 1),
        )
        == Decimal("100.0")
    )

    # Price moves inversely: 20 -> 10 -> 50.
    assert (
        normalizer.normalize_price(
            Decimal("20"),
            symbol="XYZ",
            source_date=date(2025, 1, 1),
            target_date=date(2026, 12, 1),
        )
        == Decimal("50")
    )

    assert (
        normalizer.normalize_price(
            Decimal("50"),
            symbol="XYZ",
            source_date=date(2026, 12, 1),
            target_date=date(2025, 1, 1),
        )
        == Decimal("20.0")
    )


def test_actions_between_returns_traversal_order() -> None:
    first = CorporateActionEvidence(
        symbol="XYZ",
        effective_date=date(2025, 6, 1),
        action_type=CorporateActionType.FORWARD_SPLIT,
        new_units=Decimal("2"),
        old_units=Decimal("1"),
        source="BROKER",
    )
    second = CorporateActionEvidence(
        symbol="XYZ",
        effective_date=date(2026, 6, 1),
        action_type=CorporateActionType.FORWARD_SPLIT,
        new_units=Decimal("3"),
        old_units=Decimal("1"),
        source="BROKER",
    )
    normalizer = CorporateActionNormalizer([second, first])

    assert normalizer.actions_between(
        symbol="XYZ",
        source_date=date(2025, 1, 1),
        target_date=date(2026, 12, 1),
    ) == (first, second)

    assert normalizer.actions_between(
        symbol="XYZ",
        source_date=date(2026, 12, 1),
        target_date=date(2025, 1, 1),
    ) == (second, first)


def test_empty_symbol_is_rejected() -> None:
    normalizer = CorporateActionNormalizer([NFLX_SPLIT])

    with pytest.raises(ValueError, match="symbol"):
        normalizer.normalize_quantity(
            Decimal("5"),
            symbol=" ",
            source_date=date(2025, 11, 1),
            target_date=date(2025, 12, 1),
        )
