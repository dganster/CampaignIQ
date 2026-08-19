"""Resolve historical lot provenance at the campaign data boundary."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.value_objects.instrument import Instrument


class CampaignBoundaryResolver:
    """Resolve exact pre-period lots to campaigns that started before the data."""

    def __init__(self, lot_book: LotBook) -> None:
        self.lot_book = lot_book

    def candidate_assignments(self, campaign: Campaign) -> dict[str, Lot]:
        """Return exact pre-period lot candidates without mutating the book."""
        if not campaign.started_before_data:
            return {}

        required: dict[Instrument, Decimal] = defaultdict(Decimal)

        for trade in campaign.trades:
            for leg in trade.legs:
                if leg.position_effect != PositionEffect.CLOSE:
                    continue

                quantity = sum(
                    (abs(execution.quantity) for execution in leg.executions),
                    Decimal("0"),
                )

                if quantity:
                    required[leg.instrument] += quantity

        candidates: dict[str, Lot] = {}

        for instrument, quantity in required.items():
            matching_lots = [
                lot
                for lot in self.lot_book.lots(instrument)
                if lot.campaign_id is None
            ]

            if len(matching_lots) != 1:
                return {}

            lot = matching_lots[0]

            if abs(lot.quantity) != quantity:
                return {}

            candidates[lot.lot_id] = lot

        return candidates

    def resolve(self, campaign: Campaign) -> dict[str, str]:
        """Assign exact pre-period lots to one boundary campaign.

        A campaign may claim historical lots only when it started before the
        available trade data and its observed closing activity exactly
        consumes those lots. No partial or ambiguous provenance is assigned.
        """
        candidates = self.candidate_assignments(campaign)
        if not candidates:
            return {}

        for lot in candidates.values():
            self.lot_book.assign_campaign(
                lot.lot_id,
                campaign.campaign_id,
            )

        return {
            lot_id: campaign.campaign_id
            for lot_id in candidates
        }
