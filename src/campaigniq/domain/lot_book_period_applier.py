"""Replay one period of economic activity onto an opening lot book."""

from __future__ import annotations

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.position_event import PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_history import PositionHistory


class LotBookPeriodApplier:
    """Produce ending lot state from resolved opening lots and period history."""

    def apply(
        self,
        *,
        opening_lot_book: LotBook,
        position_history: PositionHistory,
        campaigns: tuple[Campaign, ...],
    ) -> LotBook:
        """Return an independently evolved ending lot book."""

        lot_book = opening_lot_book.clone()

        campaign_ids = {
            id(trade): campaign.campaign_id
            for campaign in campaigns
            for trade in campaign.trades
        }

        for item in position_history.items():
            if item.trade is not None:
                lot_book.apply_trade(
                    item.trade,
                    campaign_id=campaign_ids.get(id(item.trade)),
                )
                continue

            assert item.event is not None
            self._apply_event(lot_book, item.event)

        return lot_book

    def _apply_event(
        self,
        lot_book: LotBook,
        event: PositionEvent,
    ) -> None:
        """Apply one event while preserving assignment provenance."""

        assignment_campaign_id: str | None = None

        for change in event.changes:
            if (
                event.kind == PositionEventKind.ASSIGNMENT
                and isinstance(change.instrument, OptionContract)
            ):
                allocations = lot_book.apply_signed_change(
                    instrument=change.instrument,
                    quantity=change.quantity,
                    occurred_at=event.occurred_at,
                )

                campaign_ids = {
                    allocation.campaign_id
                    for allocation in allocations
                    if allocation.campaign_id is not None
                }

                if len(campaign_ids) == 1:
                    assignment_campaign_id = next(iter(campaign_ids))

                continue

            if (
                event.kind == PositionEventKind.ASSIGNMENT
                and assignment_campaign_id is not None
                and change.quantity < 0
            ):
                lot_book.assign_unassigned_lots_to_campaign(
                    change.instrument,
                    quantity=abs(change.quantity),
                    campaign_id=assignment_campaign_id,
                )

            lot_book.apply_signed_change(
                instrument=change.instrument,
                quantity=change.quantity,
                occurred_at=event.occurred_at,
                campaign_id=assignment_campaign_id,
            )
