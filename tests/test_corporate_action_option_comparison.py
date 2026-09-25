from datetime import date
from decimal import Decimal

from campaigniq.domain.corporate_action import (
    CorporateActionEvidence,
    CorporateActionType,
)
from campaigniq.domain.corporate_action_normalizer import (
    CorporateActionNormalizer,
)
from campaigniq.domain.corporate_action_option_comparison import (
    CorporateActionOptionComparator,
    OptionPositionObservation,
)
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType


NFLX_SPLIT = CorporateActionEvidence(
    symbol="NFLX",
    effective_date=date(2025, 11, 17),
    action_type=CorporateActionType.FORWARD_SPLIT,
    new_units=Decimal("10"),
    old_units=Decimal("1"),
    source="SCHWAB",
    source_reference="Options Frwd Split",
)


def obs(
    *,
    symbol: str = "NFLX",
    expiration: date = date(2025, 12, 19),
    strike: str,
    option_type: OptionType = OptionType.PUT,
    quantity: str,
    observed_date: date,
) -> OptionPositionObservation:
    return OptionPositionObservation(
        contract=OptionContract(
            underlying=symbol,
            expiration=expiration,
            strike=Decimal(strike),
            option_type=option_type,
        ),
        quantity=Decimal(quantity),
        observed_date=observed_date,
    )


def comparator() -> CorporateActionOptionComparator:
    return CorporateActionOptionComparator(
        CorporateActionNormalizer([NFLX_SPLIT])
    )


def test_nflx_split_adjusted_positions_are_economically_compatible() -> None:
    before = obs(
        strike="1140",
        quantity="-5",
        observed_date=date(2025, 11, 14),
    )
    after = obs(
        strike="114",
        quantity="-50",
        observed_date=date(2025, 11, 18),
    )

    assert comparator().economically_compatible(before, after)


def test_nflx_comparison_does_not_mutate_broker_observations() -> None:
    before = obs(
        strike="1140",
        quantity="-5",
        observed_date=date(2025, 11, 14),
    )
    after = obs(
        strike="114",
        quantity="-50",
        observed_date=date(2025, 11, 18),
    )

    comparator().economically_compatible(before, after)

    assert before.contract.strike == Decimal("1140")
    assert before.quantity == Decimal("-5")
    assert after.contract.strike == Decimal("114")
    assert after.quantity == Decimal("-50")


def test_nflx_pre_split_position_normalizes_into_post_split_units() -> None:
    before = obs(
        strike="1140",
        quantity="-5",
        observed_date=date(2025, 11, 14),
    )

    normalized = comparator().normalize(
        before,
        target_date=date(2025, 11, 18),
    )

    assert normalized.underlying == "NFLX"
    assert normalized.strike == Decimal("114.0")
    assert normalized.quantity == Decimal("-50")
    assert normalized.expiration == date(2025, 12, 19)
    assert normalized.option_type is OptionType.PUT


def test_same_strike_without_adjusted_quantity_is_not_compatible() -> None:
    before = obs(
        strike="1140",
        quantity="-5",
        observed_date=date(2025, 11, 14),
    )
    after = obs(
        strike="114",
        quantity="-5",
        observed_date=date(2025, 11, 18),
    )

    assert not comparator().economically_compatible(before, after)


def test_same_quantity_without_adjusted_strike_is_not_compatible() -> None:
    before = obs(
        strike="1140",
        quantity="-5",
        observed_date=date(2025, 11, 14),
    )
    after = obs(
        strike="1140",
        quantity="-50",
        observed_date=date(2025, 11, 18),
    )

    assert not comparator().economically_compatible(before, after)


def test_different_expiration_is_not_compatible() -> None:
    before = obs(
        expiration=date(2025, 12, 19),
        strike="1140",
        quantity="-5",
        observed_date=date(2025, 11, 14),
    )
    after = obs(
        expiration=date(2026, 1, 16),
        strike="114",
        quantity="-50",
        observed_date=date(2025, 11, 18),
    )

    assert not comparator().economically_compatible(before, after)


def test_different_option_type_is_not_compatible() -> None:
    before = obs(
        strike="1140",
        quantity="-5",
        option_type=OptionType.PUT,
        observed_date=date(2025, 11, 14),
    )
    after = obs(
        strike="114",
        quantity="-50",
        option_type=OptionType.CALL,
        observed_date=date(2025, 11, 18),
    )

    assert not comparator().economically_compatible(before, after)


def test_different_underlying_is_not_compatible() -> None:
    before = obs(
        symbol="NFLX",
        strike="1140",
        quantity="-5",
        observed_date=date(2025, 11, 14),
    )
    after = obs(
        symbol="AAPL",
        strike="114",
        quantity="-50",
        observed_date=date(2025, 11, 18),
    )

    assert not comparator().economically_compatible(before, after)


def test_comparison_can_use_pre_action_target_date() -> None:
    before = obs(
        strike="1140",
        quantity="-5",
        observed_date=date(2025, 11, 14),
    )
    after = obs(
        strike="114",
        quantity="-50",
        observed_date=date(2025, 11, 18),
    )

    assert comparator().economically_compatible(
        before,
        after,
        target_date=date(2025, 11, 14),
    )


def test_no_authoritative_action_means_no_split_inference() -> None:
    no_action_comparator = CorporateActionOptionComparator(
        CorporateActionNormalizer()
    )

    before = obs(
        strike="1140",
        quantity="-5",
        observed_date=date(2025, 11, 14),
    )
    after = obs(
        strike="114",
        quantity="-50",
        observed_date=date(2025, 11, 18),
    )

    assert not no_action_comparator.economically_compatible(before, after)


def test_ordinary_same_contract_position_is_compatible_without_actions() -> None:
    no_action_comparator = CorporateActionOptionComparator(
        CorporateActionNormalizer()
    )

    first = obs(
        strike="114",
        quantity="-50",
        observed_date=date(2025, 12, 1),
    )
    second = obs(
        strike="114",
        quantity="-50",
        observed_date=date(2025, 12, 2),
    )

    assert no_action_comparator.economically_compatible(first, second)


def test_signed_quantity_matters() -> None:
    before = obs(
        strike="1140",
        quantity="-5",
        observed_date=date(2025, 11, 14),
    )
    after = obs(
        strike="114",
        quantity="50",
        observed_date=date(2025, 11, 18),
    )

    assert not comparator().economically_compatible(before, after)
