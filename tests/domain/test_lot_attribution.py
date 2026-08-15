from datetime import date
from decimal import Decimal

from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument


def test_realized_attribution_reconciles_broker_basis() -> None:
    record = RealizedGainLossRecord(
        closed_date=date(2026, 1, 23),
        instrument=Instrument("COIN"),
        quantity=Decimal("500"),
        closing_price=Decimal("217.44"),
        proceeds=Decimal("108719.90"),
        cost_basis=Decimal("128703.31"),
        gain_loss=Decimal("-19983.41"),
        basis_method="FIFO",
        term="SHORT TERM",
    )
    attribution = RealizedAttribution(
        record=record,
        allocations=(
            LotAllocation("DEC-COIN", Decimal("500"), Decimal("128703.31"), "SCHWAB_REALIZED_GAIN_LOSS"),
        ),
    )
    assert attribution.allocated_quantity == Decimal("500")
    assert attribution.basis_reconciled is True
    assert attribution.gain_loss_reconciled is True
