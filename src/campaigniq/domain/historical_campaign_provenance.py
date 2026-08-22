"""Resolve historical campaign provenance for opening option lots."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.trade import Trade


class HistoricalCampaignProvenanceResolver:
    """Assign historical campaign provenance to surviving opening option lots."""

    def __init__(self) -> None:
        self._campaign_reconstructor = CampaignReconstructor()

    def resolve(
        self,
        historical_trades: list[Trade],
        opening_lot_book: LotBook,
    ) -> None:
        """Assign unambiguous historical campaign IDs to opening option lots."""

        if not historical_trades:
            return

        historical_campaigns = self._campaign_reconstructor.reconstruct(
            historical_trades
        )

        for campaign in historical_campaigns:
            surviving = self._surviving_option_positions(
                campaign.trades
            )

            for instrument, quantity in surviving.items():
                if quantity == 0:
                    continue

                opening_lots = [
                    lot
                    for lot in opening_lot_book.lots(instrument)
                    if lot.campaign_id is None
                ]

                if not opening_lots:
                    continue

                opening_quantity = sum(
                    (lot.quantity for lot in opening_lots),
                    Decimal("0"),
                )

                if opening_quantity != quantity:
                    continue

                campaign_id = f"HIST-{campaign.campaign_id}"

                for lot in opening_lots:
                    opening_lot_book.assign_campaign(
                        lot.lot_id,
                        campaign_id,
                    )

    @staticmethod
    def _surviving_option_positions(
        trades: tuple[Trade, ...],
    ) -> dict[OptionContract, Decimal]:
        """Return each option's net surviving position after the history."""

        positions: dict[OptionContract, Decimal] = defaultdict(Decimal)

        for trade in trades:
            for leg in trade.legs:
                if not isinstance(leg.instrument, OptionContract):
                    continue

                for execution in leg.executions:
                    positions[leg.instrument] += execution.quantity

        return dict(positions)

    