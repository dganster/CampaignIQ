"""Attribute broker realized records to CampaignIQ opening lots."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from types import MappingProxyType
from collections.abc import Mapping

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.leg import Leg
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_event import PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.trade import Trade
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.value_objects.instrument import Instrument


@dataclass(frozen=True, slots=True)
class _ClosingActivity:
    closed_date: date
    instrument: Instrument
    quantity: Decimal
    allocations: tuple[LotAllocation, ...]
    is_assignment: bool = False
    execution_date: date | None = None


@dataclass(slots=True)
class _AssignmentClosure:
    event_index: int
    underlying: Instrument
    strike: Decimal
    occurred_on: date
    remaining_quantity: Decimal
    has_underlying_change: bool = False


from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor


class ChronologicalRealizedLotAttributor(RealizedLotAttributor):
    """Local rehearsal attribution; interleave trades and economic events."""
    def _attribute(
        self,
        *,
        trades: list[Trade],
        records: list[RealizedGainLossRecord],
        events: list[PositionEvent] | None,
        campaign_ids: dict[int, str] | None,
    ) -> tuple[RealizedAttribution, ...]:
        # Check alias ambiguity before touching the working lot book. If the
        # broker root is independently represented, merging it could consume a
        # different series. Mixed-root realized rows are ambiguous as well.
        represented = set(self.lot_book.instruments())
        represented.update(leg.instrument for trade in trades for leg in trade.legs)
        represented.update(change.instrument for event in events or [] for change in event.changes)
        reported = {record.instrument for record in records}
        for source, target in self._contract_aliases.items():
            if source in represented or (source in reported and target in reported):
                raise ValueError("Ambiguous SPX/SPXW evidence; explicit contract mapping cannot merge both roots.")

        activities: list[_ClosingActivity] = []
        assignment_closures = self._assignment_closures(events or [])

        timeline = [(self._trade_time(trade),0,index,trade,None) for index,trade in enumerate(trades)]
        timeline += [(event.occurred_at,1,index,None,event) for index,event in enumerate(events or [])]
        for _,_,event_index,trade,event in sorted(timeline,key=lambda row:row[:3]):
            if trade is not None:
                campaign_id = (
                    campaign_ids.get(id(trade))
                    if campaign_ids is not None
                    else None
                )

                for leg in trade.legs:
                    allocations = self.lot_book.apply_trade(
                        Trade(legs=(leg,)),
                        campaign_id=campaign_id,
                    )
                    if leg.position_effect != PositionEffect.CLOSE:
                        continue

                    is_assignment = self._matches_assignment_closure(
                        leg, assignment_closures
                    )
                    closed_date = self._economic_closed_date(
                        leg, assignment_closures
                    )

                    activities.append(
                        _ClosingActivity(
                            closed_date=closed_date,
                            instrument=leg.instrument,
                            quantity=self._leg_quantity(leg),
                            allocations=allocations,
                            is_assignment=is_assignment,
                            execution_date=self._leg_date(leg),
                        )
                    )

            else:
                if event.kind is PositionEventKind.SPLIT:
                    self.lot_book.apply_event(event)
                    continue
                assignment_campaign_id = None

                for change in event.changes:
                    quantity = change.quantity

                    if (
                        event.kind == PositionEventKind.ASSIGNMENT
                        and isinstance(change.instrument, OptionContract)
                    ):
                        allocations = self.lot_book.apply_signed_change(
                            instrument=change.instrument,
                            quantity=quantity,
                            occurred_at=event.occurred_at,
                        )

                        assignment_campaign_ids = {
                            allocation.campaign_id
                            for allocation in allocations
                            if allocation.campaign_id is not None
                        }

                        if len(assignment_campaign_ids) == 1:
                            assignment_campaign_id = next(iter(assignment_campaign_ids))

                        if allocations and quantity > 0:
                            share_allocations = tuple(
                                LotAllocation(
                                    lot_id=allocation.lot_id,
                                    quantity=allocation.quantity * Decimal("100"),
                                    broker_basis=allocation.broker_basis,
                                    basis_source=allocation.basis_source,
                                    campaign_id=allocation.campaign_id,
                                )
                                for allocation in allocations
                            )

                            activities.append(
                                _ClosingActivity(
                                    closed_date=event.occurred_at.date(),
                                    instrument=change.instrument,
                                    quantity=abs(quantity) * Decimal("100"),
                                    allocations=share_allocations,
                                    is_assignment=True,
                                )
                            )

                        continue

                    if (
                        event.kind == PositionEventKind.ASSIGNMENT
                        and not isinstance(change.instrument, OptionContract)
                        and change.quantity < 0
                    ):
                        unmatched = self._remaining_assignment_quantity(
                            assignment_closures,
                            event_index,
                            change.instrument,
                        )
                        if unmatched is not None:
                            quantity = -min(abs(change.quantity), unmatched)
                    elif (event.kind is not PositionEventKind.EXPIRATION and change.quantity < 0
                              and self._has_trade_closure(trades, change.instrument)):
                        continue

                    if quantity == 0:
                        continue

                    # A short-call assignment delivers shares away.  The shares
                    # therefore close an existing positive stock lot, and that
                    # opening lot must inherit the campaign identified from the
                    # assigned option BEFORE apply_signed_change creates its
                    # closing allocation.  Doing this afterward is too late:
                    # the allocation has already copied the lot's campaign_id.
                    if (
                        event.kind == PositionEventKind.ASSIGNMENT
                        and not isinstance(change.instrument, OptionContract)
                        and assignment_campaign_id is not None
                        and change.quantity < 0
                    ):
                        self.lot_book.assign_unassigned_lots_to_campaign(
                            change.instrument,
                            quantity=abs(change.quantity),
                            campaign_id=assignment_campaign_id,
                        )

                    allocations = self.lot_book.apply_signed_change(
                        instrument=change.instrument,
                        quantity=quantity,
                        occurred_at=event.occurred_at,
                        campaign_id=assignment_campaign_id,
                    )

                    # Expiration offsets may close either long (+lot) or short
                    # (-lot) options.  A short-option offset has a positive signed
                    # change, so checking change.quantity < 0 drops its realized
                    # closing activity even when the lot book closed a short lot.
                    if (
                        event.kind == PositionEventKind.EXPIRATION
                        and isinstance(change.instrument, OptionContract)
                        and allocations
                    ):
                        activities.append(
                            _ClosingActivity(
                                closed_date=event.occurred_at.date(),
                                instrument=change.instrument,
                                quantity=sum(
                                    (allocation.quantity for allocation in allocations),
                                    Decimal("0"),
                                ),
                                allocations=allocations,
                                # Preserve the existing event-date reconciliation
                                # for broker expiration posting dates.
                                is_assignment=True,
                            )
                        )
                    elif allocations and change.quantity < 0:
                        activities.append(
                            _ClosingActivity(
                                closed_date=event.occurred_at.date(),
                                instrument=change.instrument,
                                quantity=abs(quantity),
                                allocations=allocations,
                                is_assignment=True,
                            )
                        )

        unused = list(activities)
        results: list[RealizedAttribution] = []
        for record in records:
            matching_instrument = self._contract_aliases.get(record.instrument, record.instrument)
            matching_record = (
                replace(record, instrument=matching_instrument)
                if matching_instrument != record.instrument else record
            )
            allocations = self._consume_matching_activity(
                unused,
                matching_record,
            )
            attributed = self.lot_book.apply_realized_basis(
                allocations,
                record,
            )
            results.append(
                RealizedAttribution(
                    record=record,
                    allocations=attributed,
                )
            )

        return tuple(results)


    @classmethod
    def _next_business_day(cls,value):
        candidate=value+timedelta(days=1)
        # OCC 2025 holiday schedule, memo 55737: exchange-open bank holidays
        # defer securities settlement. This rehearsal policy is scoped to 2025.
        extra={date(2025,10,13),date(2025,11,11)}
        while candidate.weekday()>=5 or cls._is_us_market_holiday(candidate) or candidate in extra:
            candidate+=timedelta(days=1)
        return candidate
