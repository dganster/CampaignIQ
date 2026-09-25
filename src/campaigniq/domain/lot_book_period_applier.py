"""Replay one period of economic activity onto an opening lot book."""

from __future__ import annotations

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_roll import detect_option_rolls
from campaigniq.domain.position_event import PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_exit import detect_position_exit
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.position_lifecycle_transition import (
    PositionLifecycleTransition,
)


class LotBookPeriodApplier:
    """Produce ending lot state from resolved opening lots and period history."""

    def apply_with_lifecycle(
        self,
        *,
        opening_lot_book: LotBook,
        position_history: PositionHistory,
        campaigns: tuple[Campaign, ...],
    ) -> tuple[
        LotBook,
        tuple[PositionLifecycleTransition, ...],
    ]:
        """
        Return ending lot state and evidence-backed period transitions.

        Lifecycle evidence is collected during the same chronological replay
        that evolves the lot book.  It is not independently reconstructed.
        """

        lot_book = opening_lot_book.clone()
        transitions: list[PositionLifecycleTransition] = []

        campaign_ids = {
            id(trade): campaign.campaign_id
            for campaign in campaigns
            for trade in campaign.trades
        }

        for item in position_history.items():
            if item.trade is not None:
                trade = item.trade

                transitions.extend(
                    PositionLifecycleTransition.from_option_roll(roll)
                    for roll in detect_option_rolls(trade)
                )

                exits = detect_position_exit(
                    opening_lot_book=lot_book,
                    trade=trade,
                )
                transitions.extend(
                    PositionLifecycleTransition.from_position_exit(exit_)
                    for exit_ in exits
                )

                lot_book.apply_trade(
                    trade,
                    campaign_id=campaign_ids.get(id(trade)),
                )
                continue

            assert item.event is not None

            if item.event.kind is PositionEventKind.ASSIGNMENT:
                symbols = {
                    (
                        change.instrument.underlying
                        if isinstance(change.instrument, OptionContract)
                        else change.instrument.symbol
                    ).upper()
                    for change in item.event.changes
                }

                transitions.extend(
                    PositionLifecycleTransition.from_assignment(
                        symbol=symbol,
                        event=item.event,
                    )
                    for symbol in sorted(symbols)
                )

            self._apply_event(lot_book, item.event)

        return (
            lot_book,
            tuple(
                sorted(
                    transitions,
                    key=lambda transition: transition.occurred_at,
                )
            ),
        )

    def apply(
        self,
        *,
        opening_lot_book: LotBook,
        position_history: PositionHistory,
        campaigns: tuple[Campaign, ...],
    ) -> LotBook:
        """Return an independently evolved ending lot book."""

        lot_book, _ = self.apply_with_lifecycle(
            opening_lot_book=opening_lot_book,
            position_history=position_history,
            campaigns=campaigns,
        )
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
