from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable

from campaigniq.domain.corporate_action import CorporateActionEvidence


class CorporateActionNormalizer:
    """Normalize quantities and prices across authoritative corporate actions.

    Broker-reported facts are never modified.  This service derives equivalent
    quantities and prices expressed in the unit system applicable on a target
    date.

    Actions are applied when crossing their effective date:

        source_date < effective_date <= target_date

    when moving forward, and the inverse transformation is applied when moving
    backward.
    """

    def __init__(
        self,
        actions: Iterable[CorporateActionEvidence] = (),
    ) -> None:
        self._actions = tuple(
            sorted(
                actions,
                key=lambda action: (
                    action.effective_date,
                    action.symbol,
                    action.action_type.value,
                    action.new_units,
                    action.old_units,
                    action.source,
                    action.source_reference or "",
                ),
            )
        )

    @property
    def actions(self) -> tuple[CorporateActionEvidence, ...]:
        return self._actions

    def actions_between(
        self,
        *,
        symbol: str,
        source_date: date,
        target_date: date,
    ) -> tuple[CorporateActionEvidence, ...]:
        """Return actions crossed while moving from source to target date.

        Returned actions are ordered in traversal order.
        """

        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("Normalization symbol must not be empty.")

        if source_date == target_date:
            return ()

        matching = [
            action
            for action in self._actions
            if action.symbol == normalized_symbol
        ]

        if source_date < target_date:
            return tuple(
                action
                for action in matching
                if source_date < action.effective_date <= target_date
            )

        return tuple(
            reversed(
                [
                    action
                    for action in matching
                    if target_date < action.effective_date <= source_date
                ]
            )
        )

    def normalize_quantity(
        self,
        quantity: Decimal,
        *,
        symbol: str,
        source_date: date,
        target_date: date,
    ) -> Decimal:
        """Express quantity in the unit system applicable on target_date."""

        result = quantity

        if source_date == target_date:
            return result

        actions = self.actions_between(
            symbol=symbol,
            source_date=source_date,
            target_date=target_date,
        )

        if source_date < target_date:
            for action in actions:
                result *= action.unit_multiplier
        else:
            for action in actions:
                result *= action.inverse_multiplier

        return result

    def normalize_price(
        self,
        price: Decimal,
        *,
        symbol: str,
        source_date: date,
        target_date: date,
    ) -> Decimal:
        """Express a price/strike in the unit system applicable on target_date."""

        result = price

        if source_date == target_date:
            return result

        actions = self.actions_between(
            symbol=symbol,
            source_date=source_date,
            target_date=target_date,
        )

        if source_date < target_date:
            for action in actions:
                result *= action.inverse_multiplier
        else:
            for action in actions:
                result *= action.unit_multiplier

        return result
