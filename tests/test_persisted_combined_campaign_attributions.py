from datetime import date, datetime
from decimal import Decimal

import pytest

from campaigniq.domain.forex_settlement_attribution import ForexSettlementAttribution
from campaigniq.importers.schwab.forex_transaction_reader import SchwabForexSettlement
from campaigniq.persistence.forex_settlement_attribution_store import (
    save_forex_settlement_attributions,
)
from campaigniq.persistence.persisted_multi_month_analytics import (
    load_persisted_monthly_campaign_attributions,
)
from campaigniq.persistence.realized_attribution_store import (
    save_realized_attributions,
)


def _forex_attribution() -> ForexSettlementAttribution:
    return ForexSettlementAttribution(
        campaign_id="CAMP-000026",
        settlement=SchwabForexSettlement(
            order_id="12345",
            trade_at=datetime(2026, 8, 3, 10, 30),
            settlement_at=datetime(2026, 8, 5),
            instrument="EUR/USD",
            side="S",
            rate=Decimal("1.1500"),
            amount=Decimal("10000"),
            settlement_pl_usd=Decimal("3.00"),
            total_position=Decimal("0"),
        ),
    )


def test_loader_keeps_missing_forex_period_explicit(tmp_path) -> None:
    realized_path = tmp_path / "2026-08-realized-attributions.json"
    save_realized_attributions(
        realized_path,
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        attributions=(),
    )

    realized, forex = load_persisted_monthly_campaign_attributions(
        realized_paths=(realized_path,),
        forex_paths=(),
    )

    key = (date(2026, 8, 1), date(2026, 8, 31))
    assert key in realized
    assert key not in forex


def test_loader_pairs_forex_with_matching_realized_period(tmp_path) -> None:
    realized_path = tmp_path / "2026-08-realized-attributions.json"
    forex_path = tmp_path / "2026-08-forex-settlement-attributions.json"

    save_realized_attributions(
        realized_path,
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        attributions=(),
    )
    expected = _forex_attribution()
    save_forex_settlement_attributions(
        forex_path,
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        attributions=(expected,),
    )

    realized, forex = load_persisted_monthly_campaign_attributions(
        realized_paths=(realized_path,),
        forex_paths=(forex_path,),
    )

    key = (date(2026, 8, 1), date(2026, 8, 31))
    assert realized[key] == ()
    assert forex[key] == (expected,)


def test_loader_rejects_orphan_forex_period(tmp_path) -> None:
    forex_path = tmp_path / "2026-08-forex-settlement-attributions.json"
    save_forex_settlement_attributions(
        forex_path,
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        attributions=(_forex_attribution(),),
    )

    with pytest.raises(ValueError, match="no matching realized attribution period"):
        load_persisted_monthly_campaign_attributions(
            realized_paths=(),
            forex_paths=(forex_path,),
        )
