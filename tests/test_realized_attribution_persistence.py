import json
from datetime import date
from decimal import Decimal

from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.realized_attribution_store import (
    PersistedRealizedAttributions,
    load_realized_attributions,
    save_realized_attributions,
)


def test_realized_attributions_round_trip_losslessly(tmp_path) -> None:
    path = tmp_path / "realized_attributions.json"

    original = (
        RealizedAttribution(
            record=RealizedGainLossRecord(
                closed_date=date(2026, 6, 12),
                instrument=Instrument("IBM"),
                quantity=Decimal("100"),
                closing_price=Decimal("265.37"),
                proceeds=Decimal("26537.00"),
                cost_basis=Decimal("27100.25"),
                gain_loss=Decimal("-463.25"),
                basis_method="FIFO",
                term="SHORT_TERM",
                disallowed_loss=Decimal("100.00"),
            ),
            allocations=(
                LotAllocation(
                    lot_id="LOT-IBM-001",
                    quantity=Decimal("100"),
                    broker_basis=Decimal("27100.25"),
                    basis_source="SCHWAB_REALIZED_GAIN_LOSS",
                    campaign_id="HIST-CAMP-000012",
                ),
            ),
        ),
        RealizedAttribution(
            record=RealizedGainLossRecord(
                closed_date=date(2026, 8, 19),
                instrument=OptionContract(
                    underlying="GLW",
                    expiration=date(2026, 9, 18),
                    strike=Decimal("80.00"),
                    option_type=OptionType.CALL,
                ),
                quantity=Decimal("5"),
                closing_price=Decimal("5.43"),
                proceeds=Decimal("2714.31"),
                cost_basis=Decimal("5396.04"),
                gain_loss=Decimal("-2681.73"),
                basis_method="FIFO",
                term="SHORT_TERM",
            ),
            allocations=(
                LotAllocation(
                    lot_id="LOT-GLW-001",
                    quantity=Decimal("2"),
                    broker_basis=Decimal("2158.416"),
                    basis_source="SCHWAB_REALIZED_GAIN_LOSS",
                    campaign_id="CAMP-000012",
                ),
                LotAllocation(
                    lot_id="LOT-GLW-002",
                    quantity=Decimal("3"),
                    broker_basis=Decimal("3237.624"),
                    basis_source="SCHWAB_REALIZED_GAIN_LOSS",
                    campaign_id="CAMP-000012",
                ),
            ),
        ),
    )

    save_realized_attributions(
        path,
        period_start=date(2026, 6, 1),
        period_end=date(2026, 8, 31),
        attributions=original,
    )

    loaded = load_realized_attributions(path)

    assert loaded == PersistedRealizedAttributions(
        period_start=date(2026, 6, 1),
        period_end=date(2026, 8, 31),
        attributions=original,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["format"] == "campaigniq.realized_attributions"
    assert payload["version"] == 1
    assert payload["period_start"] == "2026-06-01"
    assert payload["period_end"] == "2026-08-31"


def test_load_rejects_unsupported_version(tmp_path) -> None:
    path = tmp_path / "realized_attributions.json"

    path.write_text(
        json.dumps(
            {
                "format": "campaigniq.realized_attributions",
                "version": 999,
                "attributions": [],
            }
        ),
        encoding="utf-8",
    )

    try:
        load_realized_attributions(path)
    except ValueError as exc:
        assert "Unsupported realized attribution persistence version" in str(exc)
    else:
        raise AssertionError("Expected unsupported version to be rejected.")


def test_round_trip_preserves_none_allocation_fields(tmp_path) -> None:
    path = tmp_path / "realized_attributions.json"

    original = (
        RealizedAttribution(
            record=RealizedGainLossRecord(
                closed_date=date(2026, 1, 15),
                instrument=Instrument("XYZ"),
                quantity=Decimal("100"),
                closing_price=Decimal("10.00"),
                proceeds=Decimal("1000.00"),
                cost_basis=Decimal("900.00"),
                gain_loss=Decimal("100.00"),
                basis_method="FIFO",
                term="SHORT_TERM",
            ),
            allocations=(
                LotAllocation(
                    lot_id="LOT-XYZ-001",
                    quantity=Decimal("100"),
                    broker_basis=None,
                    basis_source=None,
                    campaign_id=None,
                ),
            ),
        ),
    )

    save_realized_attributions(
        path,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        attributions=original,
    )

    loaded = load_realized_attributions(path)

    assert loaded.period_start == date(2026, 1, 1)
    assert loaded.period_end == date(2026, 1, 31)
    assert loaded.attributions == original
    assert loaded.attributions[0].allocations[0].broker_basis is None
    assert loaded.attributions[0].allocations[0].basis_source is None
    assert loaded.attributions[0].allocations[0].campaign_id is None


def test_persisted_period_rejects_end_before_start() -> None:
    try:
        PersistedRealizedAttributions(
            period_start=date(2026, 2, 1),
            period_end=date(2026, 1, 31),
            attributions=(),
        )
    except ValueError as exc:
        assert "period_end must be on or after period_start" in str(exc)
    else:
        raise AssertionError("Expected invalid persisted period to be rejected.")
