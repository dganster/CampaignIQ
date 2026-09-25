from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


class CorporateActionType(str, Enum):
    """Supported security corporate actions."""

    FORWARD_SPLIT = "FORWARD_SPLIT"
    REVERSE_SPLIT = "REVERSE_SPLIT"


@dataclass(frozen=True, slots=True)
class CorporateActionEvidence:
    """Authoritative evidence for a security corporate action.

    This object records the corporate action exactly as established by an
    external source.  It does not modify trades, positions, lots, campaigns,
    realized P&L, or any other broker fact.

    ``new_units`` and ``old_units`` express the adjustment ratio:

        new_units : old_units

    Examples:

        10-for-1 forward split -> new_units=10, old_units=1
        1-for-5 reverse split  -> new_units=1, old_units=5
    """

    symbol: str
    effective_date: date
    action_type: CorporateActionType
    new_units: Decimal
    old_units: Decimal
    source: str
    source_reference: str | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        source = self.source.strip()

        if not symbol:
            raise ValueError("Corporate action symbol must not be empty.")

        if not source:
            raise ValueError("Corporate action source must not be empty.")

        if self.new_units <= 0:
            raise ValueError("Corporate action new_units must be positive.")

        if self.old_units <= 0:
            raise ValueError("Corporate action old_units must be positive.")

        if self.new_units == self.old_units:
            raise ValueError(
                "Corporate action ratio must change the security units."
            )

        if (
            self.action_type is CorporateActionType.FORWARD_SPLIT
            and self.new_units <= self.old_units
        ):
            raise ValueError(
                "Forward split must increase units "
                "(new_units > old_units)."
            )

        if (
            self.action_type is CorporateActionType.REVERSE_SPLIT
            and self.new_units >= self.old_units
        ):
            raise ValueError(
                "Reverse split must decrease units "
                "(new_units < old_units)."
            )

        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "source", source)

        if self.source_reference is not None:
            reference = self.source_reference.strip()
            object.__setattr__(
                self,
                "source_reference",
                reference if reference else None,
            )

    @property
    def unit_multiplier(self) -> Decimal:
        """Post-action units produced by one pre-action unit."""

        return self.new_units / self.old_units

    @property
    def inverse_multiplier(self) -> Decimal:
        """Pre-action-equivalent units represented by one post-action unit."""

        return self.old_units / self.new_units

    def pre_action_equivalent_quantity(
        self,
        post_action_quantity: Decimal,
    ) -> Decimal:
        """Express a post-action quantity in pre-action-equivalent units."""

        return post_action_quantity * self.inverse_multiplier

    def pre_action_equivalent_price(
        self,
        post_action_price: Decimal,
    ) -> Decimal:
        """Express a post-action price/strike in pre-action-equivalent terms."""

        return post_action_price * self.unit_multiplier
