from datetime import date

from campaigniq.import_contract import MonthlyInputRole
from campaigniq.persistence.import_provenance import (
    MonthlyInputProvenance,
    save_monthly_import_provenance_to_storage,
    serialize_monthly_import_provenance,
)


class RecordingStorage:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    def write_text(self, key: str, content: str) -> None:
        self.writes.append((key, content))


def test_storage_save_uses_logical_key_and_exact_serializer_output() -> None:
    inputs = (
        MonthlyInputProvenance(
            role=MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS,
            sha256="abc123",
            byte_size=42,
        ),
    )
    storage = RecordingStorage()
    expected = serialize_monthly_import_provenance(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        inputs=inputs,
    )

    save_monthly_import_provenance_to_storage(
        storage,
        "2026-08-import-provenance.json",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        inputs=inputs,
    )

    assert storage.writes == [("2026-08-import-provenance.json", expected)]
