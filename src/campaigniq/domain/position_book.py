"""Derived current position state."""

from __future__ import annotations

from decimal import Decimal

from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_state import PositionState
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument


class PositionBook:
    """Track current quantities for instruments."""

    def __init__(self) -> None:
        self._states: dict[Instrument, PositionState] = {}

    def apply(self, trade: Trade) -> None:
        """Apply a trade to the current position state."""

        for leg in trade.legs:
            instrument = leg.instrument
            current = self._states.get(instrument)

            if current is None:
                if leg.position_effect == PositionEffect.CLOSE:
                    self._states[instrument] = PositionState(
                        instrument=instrument,
                        quantity=Decimal("0"),
                        quantity_known=False,
                        started_before_data=True,
                    )
                    continue

                quantity = sum(
                    (execution.quantity for execution in leg.executions),
                    Decimal("0"),
                )

                self._states[instrument] = PositionState(
                    instrument=instrument,
                    quantity=quantity,
                    quantity_known=True,
                    started_before_data=False,
                )
                continue

            if not current.quantity_known:
                if leg.position_effect == PositionEffect.CLOSE:
                    continue

                quantity = sum(
                    (execution.quantity for execution in leg.executions),
                    Decimal("0"),
                )

                self._states[instrument] = PositionState(
                    instrument=instrument,
                    quantity=quantity,
                    quantity_known=True,
                    started_before_data=current.started_before_data,
                )
                continue

            quantity = sum(
                (execution.quantity for execution in leg.executions),
                Decimal("0"),
            )

            self._states[instrument] = PositionState(
                instrument=instrument,
                quantity=current.quantity + quantity,
                quantity_known=True,
                started_before_data=current.started_before_data,
            )

    def set_state(
        self,
        *,
        instrument: Instrument,
        quantity: Decimal,
        quantity_known: bool,
        started_before_data: bool,
    ) -> None:
        """Set the derived state for an instrument."""

        self._states[instrument] = PositionState(
            instrument=instrument,
            quantity=quantity,
            quantity_known=quantity_known,
            started_before_data=started_before_data,
        )

    def state(self, instrument: Instrument) -> PositionState:
        """Return the current state for an instrument."""

        return self._states.get(
            instrument,
            PositionState(
                instrument=instrument,
                quantity=Decimal("0"),
                quantity_known=True,
                started_before_data=False,
            ),
        )

    def quantity(self, instrument: Instrument) -> Decimal:
        """Return the current quantity for an instrument."""

        return self.state(instrument).quantity

    def started_before_data(self, instrument: Instrument) -> bool:
        """Return whether the position existed before available data."""

        return self.state(instrument).started_before_data
