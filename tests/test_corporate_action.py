from datetime import date
from decimal import Decimal

import pytest

from campaigniq.domain.corporate_action import (
    CorporateActionEvidence,
    CorporateActionType,
)


def test_nflx_forward_split_preserves_broker_facts_and_exposes_ratio() -> None:
    action = CorporateActionEvidence(
        symbol="nflx",
        effective_date=date(2025, 11, 17),
        action_type=CorporateActionType.FORWARD_SPLIT,
        new_units=Decimal("10"),
        old_units=Decimal("1"),
        source="SCHWAB",
        source_reference="Options Frwd Split",
    )

    assert action.symbol == "NFLX"
    assert action.new_units == Decimal("10")
    assert action.old_units == Decimal("1")
    assert action.unit_multiplier == Decimal("10")
    assert action.inverse_multiplier == Decimal("0.1")

    # The evidence object records the transformation.  It does not mutate
    # broker quantities or prices.
    broker_pre_split_contracts = Decimal("5")
    broker_post_split_contracts = Decimal("50")
    broker_post_split_strike = Decimal("114")

    assert broker_pre_split_contracts == Decimal("5")
    assert broker_post_split_contracts == Decimal("50")
    assert broker_post_split_strike == Decimal("114")

    assert (
        action.pre_action_equivalent_quantity(
            broker_post_split_contracts
        )
        == broker_pre_split_contracts
    )

    assert (
        action.pre_action_equivalent_price(
            broker_post_split_strike
        )
        == Decimal("1140")
    )


def test_nflx_equity_quantity_can_be_compared_across_split() -> None:
    action = CorporateActionEvidence(
        symbol="NFLX",
        effective_date=date(2025, 11, 17),
        action_type=CorporateActionType.FORWARD_SPLIT,
        new_units=Decimal("10"),
        old_units=Decimal("1"),
        source="SCHWAB",
    )

    assert (
        action.pre_action_equivalent_quantity(Decimal("5000"))
        == Decimal("500")
    )


def test_reverse_split_uses_same_ratio_model() -> None:
    action = CorporateActionEvidence(
        symbol="XYZ",
        effective_date=date(2026, 1, 15),
        action_type=CorporateActionType.REVERSE_SPLIT,
        new_units=Decimal("1"),
        old_units=Decimal("5"),
        source="BROKER",
    )

    assert action.unit_multiplier == Decimal("0.2")
    assert action.inverse_multiplier == Decimal("5")

    assert (
        action.pre_action_equivalent_quantity(Decimal("20"))
        == Decimal("100")
    )

    assert (
        action.pre_action_equivalent_price(Decimal("50"))
        == Decimal("10")
    )


@pytest.mark.parametrize(
    ("new_units", "old_units"),
    [
        (Decimal("0"), Decimal("1")),
        (Decimal("-1"), Decimal("1")),
        (Decimal("1"), Decimal("0")),
        (Decimal("1"), Decimal("-1")),
        (Decimal("1"), Decimal("1")),
    ],
)
def test_corporate_action_rejects_invalid_ratios(
    new_units: Decimal,
    old_units: Decimal,
) -> None:
    with pytest.raises(ValueError):
        CorporateActionEvidence(
            symbol="XYZ",
            effective_date=date(2026, 1, 1),
            action_type=CorporateActionType.FORWARD_SPLIT,
            new_units=new_units,
            old_units=old_units,
            source="BROKER",
        )


def test_forward_split_rejects_reverse_ratio() -> None:
    with pytest.raises(ValueError, match="Forward split"):
        CorporateActionEvidence(
            symbol="XYZ",
            effective_date=date(2026, 1, 1),
            action_type=CorporateActionType.FORWARD_SPLIT,
            new_units=Decimal("1"),
            old_units=Decimal("5"),
            source="BROKER",
        )


def test_reverse_split_rejects_forward_ratio() -> None:
    with pytest.raises(ValueError, match="Reverse split"):
        CorporateActionEvidence(
            symbol="XYZ",
            effective_date=date(2026, 1, 1),
            action_type=CorporateActionType.REVERSE_SPLIT,
            new_units=Decimal("10"),
            old_units=Decimal("1"),
            source="BROKER",
        )


def test_symbol_and_source_are_required() -> None:
    with pytest.raises(ValueError, match="symbol"):
        CorporateActionEvidence(
            symbol=" ",
            effective_date=date(2026, 1, 1),
            action_type=CorporateActionType.FORWARD_SPLIT,
            new_units=Decimal("2"),
            old_units=Decimal("1"),
            source="BROKER",
        )

    with pytest.raises(ValueError, match="source"):
        CorporateActionEvidence(
            symbol="XYZ",
            effective_date=date(2026, 1, 1),
            action_type=CorporateActionType.FORWARD_SPLIT,
            new_units=Decimal("2"),
            old_units=Decimal("1"),
            source=" ",
        )


def test_blank_source_reference_normalizes_to_none() -> None:
    action = CorporateActionEvidence(
        symbol="XYZ",
        effective_date=date(2026, 1, 1),
        action_type=CorporateActionType.FORWARD_SPLIT,
        new_units=Decimal("2"),
        old_units=Decimal("1"),
        source="BROKER",
        source_reference="   ",
    )

    assert action.source_reference is None
