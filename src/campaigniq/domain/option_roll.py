"""Conservatively detect option rolls represented by one broker trade."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade


@dataclass(frozen=True, slots=True)
class OptionRoll:
    """One same-trade close/open option transition.

    Both original broker legs and contracts are retained.  This classification
    does not merge contracts, rewrite quantities, or infer rolls across
    separate trades.
    """

    underlying: str
    closed_contract: OptionContract
    opened_contract: OptionContract
    quantity: Decimal
    closed_leg: object
    opened_leg: object

    @property
    def expiration_changed(self) -> bool:
        return self.closed_contract.expiration != self.opened_contract.expiration

    @property
    def strike_changed(self) -> bool:
        return self.closed_contract.strike != self.opened_contract.strike


def _leg_quantity(leg) -> Decimal:
    return sum(
        (execution.quantity for execution in leg.executions),
        Decimal("0"),
    )


def _position_direction(leg) -> int:
    """Return economic option-position direction opened/closed by a leg.

    +1 means long option exposure.
    -1 means short option exposure.

    BUY OPEN and SELL CLOSE describe long exposure.
    SELL OPEN and BUY CLOSE describe short exposure.
    """

    if leg.position_effect is PositionEffect.OPEN:
        return 1 if leg.side is Side.BUY else -1

    if leg.position_effect is PositionEffect.CLOSE:
        return 1 if leg.side is Side.SELL else -1

    raise ValueError(f"Unsupported position effect: {leg.position_effect!r}")


def detect_option_rolls(trade: Trade) -> tuple[OptionRoll, ...]:
    """Return unambiguous same-trade option rolls.

    A candidate requires:

    * one CLOSE option leg and one OPEN option leg;
    * same underlying;
    * same option type;
    * same economic position direction;
    * same absolute execution quantity;
    * different contracts.

    Ambiguous many-to-many matches are deliberately omitted.
    """

    option_legs = [
        leg
        for leg in trade.legs
        if isinstance(leg.instrument, OptionContract)
    ]

    closing_legs = [
        leg
        for leg in option_legs
        if leg.position_effect is PositionEffect.CLOSE
    ]
    opening_legs = [
        leg
        for leg in option_legs
        if leg.position_effect is PositionEffect.OPEN
    ]

    candidates: list[tuple[object, object]] = []

    for closed_leg in closing_legs:
        closed_contract = closed_leg.instrument
        closed_quantity = abs(_leg_quantity(closed_leg))

        if closed_quantity == 0:
            continue

        for opened_leg in opening_legs:
            opened_contract = opened_leg.instrument
            opened_quantity = abs(_leg_quantity(opened_leg))

            if opened_quantity == 0:
                continue

            if closed_contract.underlying != opened_contract.underlying:
                continue
            if closed_contract.option_type is not opened_contract.option_type:
                continue
            if _position_direction(closed_leg) != _position_direction(opened_leg):
                continue
            if closed_quantity != opened_quantity:
                continue
            if closed_contract == opened_contract:
                continue

            candidates.append((closed_leg, opened_leg))

    # Do not guess when one leg could be paired with multiple candidates.
    closed_counts: dict[int, int] = {}
    opened_counts: dict[int, int] = {}

    for closed_leg, opened_leg in candidates:
        closed_counts[id(closed_leg)] = closed_counts.get(id(closed_leg), 0) + 1
        opened_counts[id(opened_leg)] = opened_counts.get(id(opened_leg), 0) + 1

    rolls: list[OptionRoll] = []

    for closed_leg, opened_leg in candidates:
        if closed_counts[id(closed_leg)] != 1:
            continue
        if opened_counts[id(opened_leg)] != 1:
            continue

        closed_contract = closed_leg.instrument
        opened_contract = opened_leg.instrument

        rolls.append(
            OptionRoll(
                underlying=closed_contract.underlying,
                closed_contract=closed_contract,
                opened_contract=opened_contract,
                quantity=abs(_leg_quantity(closed_leg)),
                closed_leg=closed_leg,
                opened_leg=opened_leg,
            )
        )

    return tuple(rolls)
