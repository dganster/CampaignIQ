"""Build realized campaign attributions from a completed period import."""

from __future__ import annotations

from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
from campaigniq.import_pipeline import PeriodImportResult


def attribute_period_realized_pnl(
    result: PeriodImportResult,
) -> tuple[RealizedAttribution, ...]:
    """Attribute one imported period's broker realized records to campaigns."""

    opening_lot_book = result.opening_lot_book.clone()

    return RealizedLotAttributor(
        opening_lot_book
    ).attribute_campaigns(
        list(result.campaigns),
        list(result.realized_gain_loss),
        list(result.attribution_events),
    )
