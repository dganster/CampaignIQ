"""Attribute broker realized records to CampaignIQ opening lots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

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


@dataclass(slots=True)
class _AssignmentClosure:
    event_index: int
    underlying: Instrument
    strike: Decimal
    occurred_on: date
    remaining_quantity: Decimal


class RealizedLotAttributor:
    """Build lot attribution from trades and broker realized records.

    CampaignIQ uses broker-reported basis as the authoritative closed-lot basis.
    Its own FIFO lot book is used to establish which opening lots are available
    and to expose ancestry gaps; it does not replace Schwab's basis calculation.
    """

    def __init__(self, lot_book: LotBook | None = None) -> None:
        self.lot_book = lot_book or LotBook()

    def attribute(
        self,
        trades: list[Trade],
        records: list[RealizedGainLossRecord],
        events: list[PositionEvent] | None = None,
    ) -> tuple[RealizedAttribution, ...]:
        """Attribute records using the original trade/event API."""
        return self._attribute(
            trades=trades,
            records=records,
            events=events,
            campaign_ids=None,
        )

    def attribute_campaigns(
        self,
        campaigns: list[Campaign],
        records: list[RealizedGainLossRecord],
        events: list[PositionEvent] | None = None,
    ) -> tuple[RealizedAttribution, ...]:
        """Attribute records while preserving each campaign's provenance."""
        trades: list[Trade] = []
        campaign_ids: dict[int, str] = {}

        for campaign in campaigns:
            for trade in campaign.trades:
                trades.append(trade)
                campaign_ids[id(trade)] = campaign.campaign_id

        return self._attribute(
            trades=trades,
            records=records,
            events=events,
            campaign_ids=campaign_ids,
        )

    def _attribute(
        self,
        *,
        trades: list[Trade],
        records: list[RealizedGainLossRecord],
        events: list[PositionEvent] | None,
        campaign_ids: dict[int, str] | None,
    ) -> tuple[RealizedAttribution, ...]:
        activities: list[_ClosingActivity] = []
        assignment_closures = self._assignment_closures(events or [])

        for trade in sorted(trades, key=self._trade_time):
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
                    )
                )

        for event_index, event in sorted(
            enumerate(events or []),
            key=lambda item: item[1].occurred_at,
        ):
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

                    campaign_ids = {
                        allocation.campaign_id
                        for allocation in allocations
                        if allocation.campaign_id is not None
                    }

                    if len(campaign_ids) == 1:
                        assignment_campaign_id = next(iter(campaign_ids))

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
                elif self._has_trade_closure(trades, change.instrument):
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

                if allocations and change.quantity < 0:
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
            allocations = self._consume_matching_activity(
                unused,
                record,
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

    @staticmethod
    def _assignment_closures(
        events: list[PositionEvent],
    ) -> list[_AssignmentClosure]:
        closures: list[_AssignmentClosure] = []
        for event_index, event in enumerate(events):
            if event.kind != PositionEventKind.ASSIGNMENT:
                continue
            for change in event.changes:
                if not isinstance(change.instrument, OptionContract):
                    continue
                closures.append(
                    _AssignmentClosure(
                        event_index=event_index,
                        underlying=Instrument(change.instrument.underlying),
                        strike=change.instrument.strike,
                        occurred_on=event.occurred_at.date(),
                        remaining_quantity=abs(change.quantity) * Decimal("100"),
                    )
                )
        return closures

    @staticmethod
    def _assignment_dates(
        events: list[PositionEvent],
    ) -> dict[Instrument, list[date]]:
        """Return assignment dates for legacy callers.

        New settlement matching uses the richer assignment-closure objects
        above so that strike and quantity remain available.
        """
        result: dict[Instrument, list[date]] = {}
        for closure in RealizedLotAttributor._assignment_closures(events):
            result.setdefault(closure.underlying, []).append(closure.occurred_on)
        for event in events:
            if event.kind != PositionEventKind.ASSIGNMENT:
                continue
            for change in event.changes:
                if isinstance(change.instrument, OptionContract):
                    continue
                result.setdefault(change.instrument, []).append(
                    event.occurred_at.date()
                )
        return result

    @staticmethod
    def _remaining_assignment_quantity(
        closures: list[_AssignmentClosure],
        event_index: int,
        instrument: Instrument,
    ) -> Decimal | None:
        matching = [
            closure
            for closure in closures
            if closure.event_index == event_index
            and closure.underlying == instrument
        ]
        if not matching:
            return None
        return sum(
            (closure.remaining_quantity for closure in matching),
            Decimal("0"),
        )

    @staticmethod
    def _has_trade_closure(
        trades: list[Trade],
        instrument: Instrument,
    ) -> bool:
        return any(
            leg.instrument == instrument
            and leg.position_effect == PositionEffect.CLOSE
            for trade in trades
            for leg in trade.legs
        )

    @classmethod
    def _matches_assignment_closure(
        cls,
        leg: Leg,
        assignment_closures: list[_AssignmentClosure],
    ) -> bool:
        if leg.position_effect != PositionEffect.CLOSE:
            return False

        if isinstance(leg.instrument, OptionContract):
            return False

        execution_price = cls._leg_execution_price(leg)
        if execution_price is None:
            execution_price = Decimal("NaN")

        trade_date = cls._leg_date(leg)

        return any(
            closure.underlying == leg.instrument
            and closure.occurred_on <= trade_date
            and trade_date <= cls._next_business_day(
                closure.occurred_on
            )
            and closure.remaining_quantity > 0
            and closure.strike == execution_price
            for closure in assignment_closures
        )

    @classmethod
    def _economic_closed_date(
        cls,
        leg: Leg,
        assignment_closures: list[_AssignmentClosure],
    ) -> date:
        trade_date = cls._leg_date(leg)

        if leg.position_effect == PositionEffect.CLOSE:
            quantity = cls._leg_quantity(leg)
            execution_price = cls._leg_execution_price(leg)
            if execution_price is None:
                execution_price = Decimal("NaN")
            candidates = [
                closure
                for closure in assignment_closures
                if closure.underlying == leg.instrument
                and closure.occurred_on <= trade_date
                and trade_date <= cls._next_business_day(closure.occurred_on)
                and closure.remaining_quantity > 0
                and closure.strike == execution_price
            ]
            if candidates:
                closure = max(candidates, key=lambda item: item.occurred_on)
                consumed = min(quantity, closure.remaining_quantity)
                closure.remaining_quantity -= consumed
                return closure.occurred_on

        if isinstance(leg.instrument, OptionContract):
            return cls._next_business_day(trade_date)

        return trade_date

    @staticmethod
    def _leg_execution_price(leg: Leg) -> Decimal | None:
        prices = [
            execution.execution_price
            for execution in leg.executions
        ]
        if not prices or any(price != prices[0] for price in prices):
            return None
        return prices[0]

    @classmethod
    def _next_business_day(cls, value: date) -> date:
        candidate = value + timedelta(days=1)
        while candidate.weekday() >= 5 or cls._is_us_market_holiday(candidate):
            candidate += timedelta(days=1)
        return candidate

    @staticmethod
    def _is_us_market_holiday(value: date) -> bool:
        if value.month == 1 and value.day == 1:
            return True
        if value.month == 1 and value.weekday() == 0 and 15 <= value.day <= 21:
            return True
        if value.month == 2 and value.weekday() == 0 and 15 <= value.day <= 21:
            return True
        if value.month == 6 and value.day == 19:
            return True
        if value.month == 7 and value.day == 4:
            return True
        if value.month == 9 and value.weekday() == 0 and 1 <= value.day <= 7:
            return True
        if value.month == 11 and value.weekday() == 3 and 22 <= value.day <= 28:
            return True
        if value.month == 12 and value.day == 25:
            return True

        a = value.year % 19
        b = value.year // 100
        c = value.year % 100
        d = b // 4
        e = b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i = c // 4
        k = c % 4
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        month = (h + l - 7 * m + 114) // 31
        day = ((h + l - 7 * m + 114) % 31) + 1
        easter = date(value.year, month, day)

        if value == easter - timedelta(days=2):
            return True

        if value.weekday() == 4:
            prior = value + timedelta(days=1)
            if prior.month == 7 and prior.day == 4:
                return True
            if prior.month == 12 and prior.day == 25:
                return True
            if prior.month == 1 and prior.day == 1:
                return True
        if value.weekday() == 0:
            prior = value - timedelta(days=1)
            if prior.month == 7 and prior.day == 4:
                return True
            if prior.month == 12 and prior.day == 25:
                return True
            if prior.month == 1 and prior.day == 1:
                return True
        return False

    @classmethod
    def _consume_matching_activity(
        cls,
        activities: list[_ClosingActivity],
        record: RealizedGainLossRecord,
    ) -> tuple[LotAllocation, ...]:
        """Consume closing activity matching one broker realized record.

        Schwab can report the realized stock sale from an option assignment
        one business day before the assignment position event.  That
        tolerance is restricted to assignment-derived stock activity; normal
        trade closures still require an exact realized date.
        """
        remaining = record.quantity
        matched: list[LotAllocation] = []

        index = 0
        while index < len(activities) and remaining > 0:
            activity = activities[index]
            if activity.instrument != record.instrument:
                index += 1
                continue

            exact_date = activity.closed_date == record.closed_date
            prior_business_day = (
                activity.is_assignment
                and cls._next_business_day(record.closed_date)
                == activity.closed_date
            )
            assignment_holiday_settlement = (
                activity.is_assignment
                and activity.closed_date
                == record.closed_date + timedelta(days=1)
                and cls._is_us_market_holiday(activity.closed_date)
            )
            if not (
                exact_date
                or prior_business_day
                or assignment_holiday_settlement
            ):
                index += 1
                continue

            take = min(activity.quantity, remaining)
            matched.extend(
                cls._take_allocations(activity.allocations, take)
            )

            if take == activity.quantity:
                activities.pop(index)
            else:
                activities[index] = _ClosingActivity(
                    closed_date=activity.closed_date,
                    instrument=activity.instrument,
                    quantity=activity.quantity - take,
                    allocations=cls._remaining_allocations(
                        activity.allocations,
                        take,
                    ),
                    is_assignment=activity.is_assignment,
                )
                index += 1

            remaining -= take

        if remaining:
            raise ValueError(
                "No CampaignIQ closing activity matches broker realized "
                f"record: {record.instrument} {record.closed_date} "
                f"{record.quantity}; {remaining} remains unmatched."
            )

        return tuple(matched)

    @staticmethod
    def _take_allocations(
        allocations: tuple[LotAllocation, ...],
        quantity: Decimal,
    ) -> list[LotAllocation]:
        remaining = quantity
        result: list[LotAllocation] = []
        for allocation in allocations:
            if remaining == 0:
                break
            take = min(allocation.quantity, remaining)
            result.append(
                LotAllocation(
                    lot_id=allocation.lot_id,
                    quantity=take,
                    broker_basis=None,
                    campaign_id=allocation.campaign_id,
                )
            )
            remaining -= take
        if remaining:
            raise ValueError("Closing allocation quantity is inconsistent.")
        return result

    @staticmethod
    def _remaining_allocations(
        allocations: tuple[LotAllocation, ...],
        consumed: Decimal,
    ) -> tuple[LotAllocation, ...]:
        remaining = consumed
        result: list[LotAllocation] = []
        for allocation in allocations:
            if remaining:
                take = min(allocation.quantity, remaining)
                left = allocation.quantity - take
                remaining -= take
            else:
                left = allocation.quantity
            if left:
                result.append(
                    LotAllocation(
                        lot_id=allocation.lot_id,
                        quantity=left,
                        broker_basis=None,
                        campaign_id=allocation.campaign_id,
                    )
                )
        if remaining:
            raise ValueError("Closing allocation quantity is inconsistent.")
        return tuple(result)

    @staticmethod
    def _trade_time(trade: Trade):
        return min(
            execution.executed_at
            for leg in trade.legs
            for execution in leg.executions
        )

    @staticmethod
    def _leg_date(leg: Leg) -> date:
        return min(
            execution.executed_at for execution in leg.executions
        ).date()

    @staticmethod
    def _leg_quantity(leg: Leg) -> Decimal:
        return sum(
            (abs(execution.quantity) for execution in leg.executions),
            Decimal("0"),
        )
