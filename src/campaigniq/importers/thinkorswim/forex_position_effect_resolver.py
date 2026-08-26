"""Resolve economic position effects for Thinkorswim Forex executions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from campaigniq.domain.position_effect import PositionEffect


@dataclass(frozen=True, slots=True)
class ForexPositionEffect:
    """One economic effect produced by a Forex execution."""

    position_effect: PositionEffect
    quantity: Decimal


class ForexPositionEffectResolver:
    """Resolve economic position effects for Forex executions."""

    def __init__(
        self,
        initial_positions: dict[str, Decimal] | None = None,
    ) -> None:
        self._positions = dict(initial_positions or {})

    def resolve(
        self,
        *,
        pair: str,
        quantity: Decimal,
    ) -> tuple[ForexPositionEffect, ...]:
        """Resolve one execution into one or more economic effects."""

        current = self._positions.get(pair, Decimal("0"))
        resulting = current + quantity

        if current == 0:
            self._positions[pair] = resulting
            return (
                ForexPositionEffect(
                    position_effect=PositionEffect.OPEN,
                    quantity=quantity,
                ),
            )

        same_direction = (
            (current > 0 and quantity > 0)
            or (current < 0 and quantity < 0)
        )

        if same_direction:
            self._positions[pair] = resulting
            return (
                ForexPositionEffect(
                    position_effect=PositionEffect.OPEN,
                    quantity=quantity,
                ),
            )

        if abs(quantity) <= abs(current):
            self._positions[pair] = resulting
            return (
                ForexPositionEffect(
                    position_effect=PositionEffect.CLOSE,
                    quantity=quantity,
                ),
            )

        close_quantity = -current
        open_quantity = resulting

        self._positions[pair] = resulting

        return (
            ForexPositionEffect(
                position_effect=PositionEffect.CLOSE,
                quantity=close_quantity,
            ),
            ForexPositionEffect(
                position_effect=PositionEffect.OPEN,
                quantity=open_quantity,
            ),
        )