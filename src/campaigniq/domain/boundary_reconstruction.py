"""Derive historical boundary issues from reconstructed campaigns."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from campaigniq.domain.boundary_validation import (
    HistoricalRequirement,
    month_labels,
)
from campaigniq.domain.campaign import Campaign
from campaigniq.domain.campaign_boundary_resolver import CampaignBoundaryResolver
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.trade import Trade


@dataclass(frozen=True, slots=True)
class BoundaryReconstruction:
    """Historical boundary findings derived from campaigns and opening lots."""

    unresolved_positions: tuple[str, ...]
    unresolved_campaigns: tuple[str, ...]
    historical_requirements: tuple[HistoricalRequirement, ...]


class BoundaryReconstructionAnalyzer:
    """Resolve historical position and campaign ancestry at a boundary."""

    DOCUMENT_TYPES = (
        "Account Trade History",
        "Brokerage Statement",
    )

    def analyze(
        self,
        *,
        period_start: date,
        campaigns: tuple[Campaign, ...] | list[Campaign],
        opening_lot_book: LotBook,
        historical_trades: tuple[Trade, ...] | list[Trade] = (),
        historical_period_start: date | None = None,
    ) -> BoundaryReconstruction:
        """Resolve boundary ancestry and derive only genuinely missing history.

        Opening snapshots establish position ancestry.  Historical trades are
        additionally required to establish campaign ancestry for a position
        that entered the account before the selected period.  This distinction
        prevents a snapshot from being mistaken for evidence of when a
        campaign began.
        """
        resolver = CampaignBoundaryResolver(opening_lot_book)
        unresolved_campaigns: list[str] = []
        unresolved_positions: set[str] = set()
        requirements: list[HistoricalRequirement] = []

        request_date = period_start - timedelta(days=1)
        request_month = month_labels(request_date, request_date)
        earlier_request_date = (
            historical_period_start - timedelta(days=1)
            if historical_period_start is not None
            else request_date
        )
        earlier_request_month = month_labels(
            earlier_request_date,
            earlier_request_date,
        )

        for campaign in campaigns:
            if not campaign.started_before_data:
                continue

            historically_proven = (
                bool(historical_trades)
                and self._campaign_ancestry_established(
                    campaign,
                    opening_lot_book,
                    historical_trades,
                )
            )

            candidates = resolver.candidate_assignments(
                campaign,
                allow_partial=historically_proven,
            )
            position_resolved = bool(candidates)
            if not position_resolved:
                closing_instruments = self._closing_instruments(campaign)
                if not closing_instruments:
                    continue

                unresolved_campaigns.append(campaign.campaign_id)
                unresolved_positions.update(closing_instruments)
                requirements.append(
                    self._requirement(
                        campaign.campaign_id,
                        request_date,
                        request_month,
                        closing_instruments,
                        "Opening position ancestry cannot be established from "
                        "the supplied opening inventory for",
                    )
                )
                continue

            if historical_trades and not historically_proven:
                unresolved_campaigns.append(campaign.campaign_id)
                closing_instruments = self._closing_instruments(campaign)
                requirements.append(
                    self._requirement(
                        campaign.campaign_id,
                        earlier_request_date,
                        earlier_request_month,
                        closing_instruments,
                        "The opening position is known, but the supplied "
                        "history does not establish when the campaign began for",
                    )
                )
                continue

            resolver.resolve(
                campaign,
                allow_partial=historically_proven,
            )

        return BoundaryReconstruction(
            unresolved_positions=tuple(sorted(unresolved_positions)),
            unresolved_campaigns=tuple(unresolved_campaigns),
            historical_requirements=tuple(requirements),
        )

    @staticmethod
    def _closing_instruments(campaign: Campaign) -> set[str]:
        return {
            BoundaryReconstructionAnalyzer._instrument_label(leg.instrument)
            for trade in campaign.trades
            for leg in trade.legs
            if leg.position_effect == PositionEffect.CLOSE
        }

    @staticmethod
    def _required_quantities(campaign: Campaign) -> dict[object, Decimal]:
        required: dict[object, Decimal] = defaultdict(Decimal)
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
        return required

    @classmethod
    def _campaign_ancestry_established(
        cls,
        campaign: Campaign,
        opening_lot_book: LotBook,
        historical_trades: tuple[Trade, ...] | list[Trade],
    ) -> bool:
        """Return true when supplied history explains the opening lots."""
        required = cls._required_quantities(campaign)

        for instrument in required:
            lots = opening_lot_book.lots(instrument)
            if not lots:
                continue

            opening_quantity = sum(
                (lot.quantity for lot in lots),
                Decimal("0"),
            )

            historical_quantity = cls._historical_opening_quantity(
                instrument,
                historical_trades,
            )

            if historical_quantity == opening_quantity:
                return True

        return False

    @staticmethod
    def _historical_opening_quantity(
        instrument: object,
        historical_trades: tuple[Trade, ...] | list[Trade],
    ) -> Decimal:
        return sum(
            (
                execution.quantity
                for trade in historical_trades
                for leg in trade.legs
                if (
                    leg.instrument == instrument
                    and leg.position_effect == PositionEffect.OPEN
                )
                for execution in leg.executions
            ),
            Decimal("0"),
        )

    @classmethod
    def _requirement(
        cls,
        case_id: str,
        request_date: date,
        request_month: tuple[str, ...],
        closing_instruments: set[str],
        reason_prefix: str,
    ) -> HistoricalRequirement:
        return HistoricalRequirement(
            case_id=case_id,
            earliest_unresolved_date=request_date,
            months=request_month,
            document_types=cls.DOCUMENT_TYPES,
            reason=(
                f"{reason_prefix} "
                f"{', '.join(sorted(closing_instruments))}."
            ),
        )

    @staticmethod
    def _instrument_label(instrument: object) -> str:
        """Return a stable human-readable instrument identity."""
        return str(instrument)
