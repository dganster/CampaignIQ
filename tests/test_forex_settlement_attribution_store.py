from datetime import date, datetime
from decimal import Decimal

import pytest

from campaigniq.domain.forex_settlement_attribution import ForexSettlementAttribution
from campaigniq.importers.schwab.forex_transaction_reader import SchwabForexSettlement
from campaigniq.persistence.forex_settlement_attribution_store import (
    load_forex_settlement_attributions,
    save_forex_settlement_attributions,
)


def _attribution() -> ForexSettlementAttribution:
    return ForexSettlementAttribution(
        campaign_id="CAMP-000026",
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


def test_round_trip_forex_settlement_attributions(tmp_path) -> None:
    path = tmp_path / "2026-08-forex-settlement-attributions.json"
    expected = _attribution()

    save_forex_settlement_attributions(
        path,
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        attributions=(expected,),
    )

    persisted = load_forex_settlement_attributions(path)

    assert persisted.period_start == date(2026, 8, 1)
    assert persisted.period_end == date(2026, 8, 31)
    assert persisted.attributions == (expected,)


def test_rejects_invalid_period() -> None:
    with pytest.raises(ValueError, match="period_end must be on or after period_start"):
        save_forex_settlement_attributions(
            "unused.json",
            period_start=date(2026, 9, 1),
            period_end=date(2026, 8, 31),
            attributions=(),
        )
