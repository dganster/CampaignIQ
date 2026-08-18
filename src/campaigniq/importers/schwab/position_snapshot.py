"""Translate Schwab position-snapshot rows into pre-period lots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.lot import Lot
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.value_objects.instrument import Instrument


@dataclass(frozen=True, slots=True)
class SchwabPositionSnapshotRow:
    """One normalized position row from a Schwab snapshot."""

    symbol: str
    quantity: Decimal
    snapshot_at: datetime
    expiration: date | None = None
    strike: Decimal | None = None
    option_type: OptionType | None = None
    basis_total: Decimal | None = None

    def instrument(self) -> Instrument | OptionContract:
        """Return the domain instrument represented by the row."""
        option_fields = (
            self.expiration,
            self.strike,
            self.option_type,
        )
        if any(value is not None for value in option_fields):
            if not all(value is not None for value in option_fields):
                raise ValueError(
                    "Option snapshot rows require expiration, strike, "
                    "and option type together."
                )
            return OptionContract(
                underlying=self.symbol,
                expiration=self.expiration,
                strike=self.strike,
                option_type=self.option_type,
            )
        return Instrument(self.symbol)


def to_lot(row: SchwabPositionSnapshotRow) -> Lot:
    """Convert an authoritative snapshot row into a seeded pre-period lot."""
    if row.quantity == 0:
        raise ValueError("Snapshot position quantity cannot be zero.")

    instrument = row.instrument()
    kind = "OPTION" if isinstance(instrument, OptionContract) else "EQUITY"
    lot_id = f"SCHWAB-SNAPSHOT:{row.snapshot_at.isoformat()}:{kind}:{row.symbol}"
    if isinstance(instrument, OptionContract):
        lot_id = (
            f"{lot_id}:{instrument.expiration.isoformat()}"
            f":{instrument.option_type.value}:{instrument.strike}"
        )

    return Lot(
        lot_id=lot_id,
        instrument=instrument,
        quantity=row.quantity,
        opened_at=row.snapshot_at,
        basis_total=row.basis_total,
        basis_source="SCHWAB_POSITION_SNAPSHOT" if row.basis_total is not None else None,
    )


def to_lots(rows: list[SchwabPositionSnapshotRow]) -> tuple[Lot, ...]:
    """Convert snapshot rows into lots, preserving row order."""
    return tuple(to_lot(row) for row in rows)
