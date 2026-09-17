from datetime import date
from decimal import Decimal

from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.realized_attribution_store import (
    PersistedRealizedAttributions,
    deserialize_realized_attributions,
    load_realized_attributions_from_storage,
    save_realized_attributions_to_storage,
    serialize_realized_attributions,
)


class MemoryStorage:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def write_text(self, key: str, content: str) -> None:
        self.values[key] = content

    def read_text(self, key: str) -> str:
        return self.values[key]


def _attributions() -> tuple[RealizedAttribution, ...]:
    return (
        RealizedAttribution(
            record=RealizedGainLossRecord(
                closed_date=date(2026, 8, 19),
                instrument=Instrument("IBM"),
                quantity=Decimal("100"),
                closing_price=Decimal("265.37"),
                proceeds=Decimal("26537.00"),
                cost_basis=Decimal("27100.25"),
                gain_loss=Decimal("-563.25"),
                basis_method="FIFO",
                term="SHORT_TERM",
            ),
            allocations=(
                LotAllocation(
                    lot_id="LOT-IBM-001",
                    quantity=Decimal("100"),
                    broker_basis=Decimal("27100.25"),
                    basis_source="SCHWAB_REALIZED_GAIN_LOSS",
                    campaign_id="CAMP-000012",
                ),
            ),
        ),
    )


def test_serializer_round_trips_without_filesystem_io() -> None:
    original = _attributions()
    text = serialize_realized_attributions(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        attributions=original,
    )
    assert deserialize_realized_attributions(text) == PersistedRealizedAttributions(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        attributions=original,
    )


def test_storage_round_trip_uses_logical_key() -> None:
    storage = MemoryStorage()
    original = _attributions()
    save_realized_attributions_to_storage(
        storage,
        "2026-08-realized-attributions.json",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        attributions=original,
    )
    loaded = load_realized_attributions_from_storage(
        storage, "2026-08-realized-attributions.json"
    )
    assert loaded.attributions == original
    assert tuple(storage.values) == ("2026-08-realized-attributions.json",)
