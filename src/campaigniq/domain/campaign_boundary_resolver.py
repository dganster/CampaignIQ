"""Resolve historical lot provenance at the campaign data boundary."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from itertools import combinations

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.value_objects.instrument import Instrument


class CampaignBoundaryResolver:
    """Resolve exact pre-period lots to campaigns that started before the data."""

    def __init__(self, lot_book: LotBook) -> None:
        self.lot_book = lot_book

    @staticmethod
    def required_opening_quantities(
        campaign: Campaign,
    ) -> dict[Instrument, Decimal]:
        """Return quantities that must have existed before the period."""
        available: dict[Instrument, Decimal] = defaultdict(Decimal)
        required: dict[Instrument, Decimal] = defaultdict(Decimal)

        for trade in campaign.trades:
            for leg in trade.legs:
                quantity = sum(
                    (abs(execution.quantity) for execution in leg.executions),
                    Decimal("0"),
                )

                if not quantity:
                    continue

                if leg.position_effect == PositionEffect.OPEN:
                    available[leg.instrument] += quantity
                    continue

                if leg.position_effect != PositionEffect.CLOSE:
                    continue

                covered = min(available[leg.instrument], quantity)
                available[leg.instrument] -= covered

                uncovered = quantity - covered
                if uncovered:
                    required[leg.instrument] += uncovered

        return dict(required)

    def candidate_assignments(
        self,
        campaign: Campaign,
        *,
        allow_partial: bool = False,
    ) -> dict[str, Lot]:
        """Return pre-period lot candidates without mutating the book."""
        if not campaign.started_before_data:
            return {}

        required = self.required_opening_quantities(campaign)
        candidates: dict[str, Lot] = {}
        provenance_campaign_ids: set[str] = set()
        ambiguous: list[
            tuple[
                Decimal,
                list[tuple[Lot, ...]],
            ]
        ] = []

        def add_selected_lots(
            selected_lots: list[Lot] | tuple[Lot, ...],
        ) -> None:
            for lot in selected_lots:
                candidates[lot.lot_id] = lot

                if lot.campaign_id is not None:
                    provenance_campaign_ids.add(
                        lot.campaign_id
                    )

        for instrument, quantity in required.items():
            matching_lots = list(
                self.lot_book.lots(instrument)
            )

            if not matching_lots:
                return {}

            total_quantity = sum(
                (abs(lot.quantity) for lot in matching_lots),
                Decimal("0"),
            )

            if total_quantity < quantity:
                return {}

            if total_quantity == quantity:
                add_selected_lots(matching_lots)
                continue

            if not allow_partial:
                return {}

            exact_subsets: list[tuple[Lot, ...]] = []

            for subset_size in range(
                1,
                len(matching_lots) + 1,
            ):
                for subset in combinations(
                    matching_lots,
                    subset_size,
                ):
                    subset_quantity = sum(
                        (
                            abs(lot.quantity)
                            for lot in subset
                        ),
                        Decimal("0"),
                    )

                    if subset_quantity == quantity:
                        exact_subsets.append(subset)

            if len(exact_subsets) == 1:
                add_selected_lots(exact_subsets[0])
                continue

            if len(exact_subsets) > 1:
                ambiguous.append(
                    (
                        quantity,
                        exact_subsets,
                    )
                )
                continue

            if (
                len(matching_lots) == 1
                and abs(matching_lots[0].quantity)
                > quantity
            ):
                add_selected_lots(matching_lots)
                continue

            return {}

        while ambiguous:
            remaining = []
            made_progress = False

            for quantity, exact_subsets in ambiguous:
                compatible_subsets = [
                    subset
                    for subset in exact_subsets
                    if all(
                        lot.campaign_id
                        in provenance_campaign_ids
                        for lot in subset
                    )
                ]

                if len(compatible_subsets) == 1:
                    add_selected_lots(
                        compatible_subsets[0]
                    )
                    made_progress = True
                else:
                    remaining.append(
                        (
                            quantity,
                            exact_subsets,
                        )
                    )

            if not made_progress:
                return {}

            ambiguous = remaining

        return candidates

    def resolve(
        self,
        campaign: Campaign,
        *,
        allow_partial: bool = False,
    ) -> dict[str, str]:
        """Assign pre-period lots to one boundary campaign."""
        candidates = self.candidate_assignments(
            campaign,
            allow_partial=allow_partial,
        )
        if not candidates:
            return {}

        required = self.required_opening_quantities(campaign)

        for lot in candidates.values():
            # A carried lot may already have proven campaign provenance from the
            # preceding period.  That proves the opening position exists, but
            # campaign IDs are reconstruction-local, so rebind the lot to the
            # current period's campaign once boundary ancestry is established.
            quantity = required[lot.instrument]

            if lot.campaign_id is not None:
                if allow_partial and abs(lot.quantity) > quantity:
                    self.lot_book.split_and_reassign_campaign(
                        lot.lot_id,
                        quantity=quantity,
                        campaign_id=campaign.campaign_id,
                    )
                else:
                    self.lot_book.reassign_campaign(
                        lot.lot_id,
                        campaign.campaign_id,
                    )
                continue

            if allow_partial and abs(lot.quantity) > quantity:
                self.lot_book.split_and_assign_campaign(
                    lot.lot_id,
                    quantity=quantity,
                    campaign_id=campaign.campaign_id,
                )
            else:
                self.lot_book.assign_campaign(
                    lot.lot_id,
                    campaign.campaign_id,
                )

        return {
            lot_id: campaign.campaign_id
            for lot_id in candidates
        }