"""Detect covered-call position state from authoritative position facts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.value_objects.instrument import Instrument


STANDARD_OPTION_MULTIPLIER = Decimal("100")


@dataclass(frozen=True, slots=True)
class PositionQuantity:
    """One signed position quantity observed at a point in time."""

    instrument: Instrument | OptionContract
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class CoveredCallPosition:
    """Derived coverage state for one underlying.

    Quantities remain in broker-reported units.  This object says only that
    long shares are sufficient to cover some or all short standard calls.
    It does not infer assignment, strategy intent, campaign identity, or
    acquisition provenance.
    """

    underlying: str
    share_quantity: Decimal
    short_call_quantity: Decimal
    required_share_quantity: Decimal
    covered_call_quantity: Decimal
    uncovered_call_quantity: Decimal
    excess_share_quantity: Decimal

    @property
    def fully_covered(self) -> bool:
        return (
            self.short_call_quantity > 0
            and self.uncovered_call_quantity == 0
        )


def detect_covered_call_position(
    positions: tuple[PositionQuantity, ...] | list[PositionQuantity],
    *,
    underlying: str,
    contract_multiplier: Decimal = STANDARD_OPTION_MULTIPLIER,
) -> CoveredCallPosition:
    """Derive covered-call state for an underlying.

    Broker/domain signs are preserved on input:

    * positive equity quantity = long shares
    * negative call quantity = short calls

    The returned short-call quantities are positive magnitudes because they
    describe coverage requirements rather than trade direction.
    """

    symbol = underlying.strip().upper()
    if not symbol:
        raise ValueError("Covered-position underlying must not be empty.")
    if contract_multiplier <= 0:
        raise ValueError("Contract multiplier must be positive.")

    share_quantity = Decimal("0")
    short_call_quantity = Decimal("0")

    for position in positions:
        instrument = position.instrument

        if isinstance(instrument, OptionContract):
            if instrument.underlying.strip().upper() != symbol:
                continue
            if instrument.option_type is not OptionType.CALL:
                continue
            if position.quantity < 0:
                short_call_quantity += -position.quantity
            continue

        if instrument.symbol.strip().upper() != symbol:
            continue
        if position.quantity > 0:
            share_quantity += position.quantity

    required_share_quantity = short_call_quantity * contract_multiplier

    if contract_multiplier == 0:
        covered_call_quantity = Decimal("0")
    else:
        covered_call_quantity = min(
            short_call_quantity,
            share_quantity / contract_multiplier,
        )

    uncovered_call_quantity = (
        short_call_quantity - covered_call_quantity
    )
    excess_share_quantity = max(
        Decimal("0"),
        share_quantity - required_share_quantity,
    )

    return CoveredCallPosition(
        underlying=symbol,
        share_quantity=share_quantity,
        short_call_quantity=short_call_quantity,
        required_share_quantity=required_share_quantity,
        covered_call_quantity=covered_call_quantity,
        uncovered_call_quantity=uncovered_call_quantity,
        excess_share_quantity=excess_share_quantity,
    )
