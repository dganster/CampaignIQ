"""Attribution of a closed quantity to an opening lot."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class LotAllocation:
    """One portion of a closing transaction attributed to an opening lot."""

    lot_id: str
    quantity: Decimal
    broker_basis: Decimal | None
    basis_source: str | None = None
    campaign_id: str | None = None

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("Allocation quantity must be positive.")
        if self.broker_basis is not None and self.broker_basis < 0:
            raise ValueError("Broker basis cannot be negative.")
