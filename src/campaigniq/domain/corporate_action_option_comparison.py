from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from campaigniq.domain.corporate_action_normalizer import (
    CorporateActionNormalizer,
)
from campaigniq.domain.option_contract import OptionContract


@dataclass(frozen=True, slots=True)
class OptionPositionObservation:
    """One broker-reported observation of an option position.

    Quantity and contract are preserved exactly as reported.  Corporate-action
    normalization is used only when comparing observations across dates.
    """

    contract: OptionContract
    quantity: Decimal
    observed_date: date


@dataclass(frozen=True, slots=True)
class NormalizedOptionPosition:
    """Derived option-position representation in a target date's unit system."""

    underlying: str
    expiration: date
    option_type: object
    strike: Decimal
    quantity: Decimal
    target_date: date


class CorporateActionOptionComparator:
    """Compare option observations across authoritative corporate actions.

    This class establishes economic compatibility, not broker-contract
    identity.  It deliberately leaves both source observations untouched.
    """

    def __init__(self, normalizer: CorporateActionNormalizer) -> None:
        self._normalizer = normalizer

    def normalize(
        self,
        observation: OptionPositionObservation,
        *,
        target_date: date,
    ) -> NormalizedOptionPosition:
        contract = observation.contract

        return NormalizedOptionPosition(
            underlying=contract.underlying.strip().upper(),
            expiration=contract.expiration,
            option_type=contract.option_type,
            strike=self._normalizer.normalize_price(
                contract.strike,
                symbol=contract.underlying,
                source_date=observation.observed_date,
                target_date=target_date,
            ),
            quantity=self._normalizer.normalize_quantity(
                observation.quantity,
                symbol=contract.underlying,
                source_date=observation.observed_date,
                target_date=target_date,
            ),
            target_date=target_date,
        )

    def economically_compatible(
        self,
        left: OptionPositionObservation,
        right: OptionPositionObservation,
        *,
        target_date: date | None = None,
    ) -> bool:
        """Whether observations represent compatible adjusted option exposure.

        Compatibility requires the same underlying, expiration and option type,
        plus equal strike and quantity after both observations are expressed in
        the same target-date unit system.

        The method does not assert that the broker or OCC considered the two
        observations the same legal contract.
        """

        comparison_date = target_date or max(
            left.observed_date,
            right.observed_date,
        )

        left_normalized = self.normalize(
            left,
            target_date=comparison_date,
        )
        right_normalized = self.normalize(
            right,
            target_date=comparison_date,
        )

        return (
            left_normalized.underlying == right_normalized.underlying
            and left_normalized.expiration == right_normalized.expiration
            and left_normalized.option_type == right_normalized.option_type
            and left_normalized.strike == right_normalized.strike
            and left_normalized.quantity == right_normalized.quantity
        )
