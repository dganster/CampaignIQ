from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.forex_settlement_attribution import ForexSettlementAttribution
from campaigniq.importers.schwab.forex_transaction_reader import SchwabForexSettlement
from campaigniq.persistence.forex_settlement_attribution_store import (
    PersistedForexSettlementAttributions,
    deserialize_forex_settlement_attributions,
    load_forex_settlement_attributions_from_storage,
    save_forex_settlement_attributions_to_storage,
    serialize_forex_settlement_attributions,
)


class MemoryStorage:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def write_text(self, key: str, content: str) -> None:
        self.values[key] = content

    def read_text(self, key: str) -> str:
        return self.values[key]


def _attribution() -> ForexSettlementAttribution:
    return ForexSettlementAttribution(
        campaign_id="FX-CAMP-000001",
        settlement=SchwabForexSettlement(
            order_id="12345",
            trade_at=datetime(2026, 8, 3, 10, 30),
            settlement_at=datetime(2026, 8, 5, 0, 0),
            instrument="EUR/USD",
            side="S",
            rate=Decimal("1.1500"),
            amount=Decimal("10000"),
            settlement_pl_usd=Decimal("3.00"),
            total_position=Decimal("0"),
        ),
    )


def test_serializer_round_trips_without_filesystem_io() -> None:
    original = (_attribution(),)
    text = serialize_forex_settlement_attributions(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        attributions=original,
    )
    assert deserialize_forex_settlement_attributions(text) == (
        PersistedForexSettlementAttributions(
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            attributions=original,
        )
    )


def test_storage_round_trip_uses_logical_key() -> None:
    storage = MemoryStorage()
    original = (_attribution(),)
    save_forex_settlement_attributions_to_storage(
        storage,
        "2026-08-forex-settlement-attributions.json",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        attributions=original,
    )
    loaded = load_forex_settlement_attributions_from_storage(
        storage,
        "2026-08-forex-settlement-attributions.json",
    )
    assert loaded.attributions == original
    assert tuple(storage.values) == ("2026-08-forex-settlement-attributions.json",)
