"""Attribute broker realized records to CampaignIQ opening lots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from campaigniq.domain.leg import Leg
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_event import PositionEvent
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
        activities: list[_ClosingActivity] = []
        assignment_dates = self._assignment_dates(events or [])

        # Trades remain the source of actual lot consumption. Assignment
        # events provide the economic closure date when Schwab's realized
        # record is dated on the assignment but the corresponding stock
        # transaction appears later in Account Trade History.
        for trade in sorted(trades, key=self._trade_time):
            for leg in trade.legs:
                allocations = self.lot_book.apply_trade(Trade(legs=(leg,)))
                if leg.position_effect != PositionEffect.CLOSE:
                    continue
                activities.append(
                    _ClosingActivity(
                        closed_date=self._economic_closed_date(
                            leg, assignment_dates
                        ),
                        instrument=leg.instrument,
                        quantity=self._leg_quantity(leg),
                        allocations=allocations,
                    )
                )

        # Assignment events also close the option lot. We need that closure
        # in the lot book, but the underlying stock change is represented by
        # Schwab's later stock transaction and must not be counted twice here.
        for event in sorted(events or [], key=lambda item: item.occurred_at):
            for change in event.changes:
                # If the broker also reports an explicit closing trade for
                # this instrument, that trade is the source of lot
                # consumption. The event supplies the economic date through
                # assignment_dates above, so applying the event again would
                # double-consume the lot.
                if self._has_trade_closure(trades, change.instrument):
                    continue

                allocations = self.lot_book.apply_signed_change(
                    instrument=change.instrument,
                    quantity=change.quantity,
                    occurred_at=event.occurred_at,
                )

                # Some broker economic events (notably option assignment)
                # are the only source of the underlying stock disposition.
                # Preserve those allocations as closing activity so a broker
                # realized record can be matched even when no stock sale
                # exists in Account Trade History.
                if allocations and change.quantity < 0:
                    activities.append(
                        _ClosingActivity(
                            closed_date=event.occurred_at.date(),
                            instrument=change.instrument,
                            quantity=abs(change.quantity),
                            allocations=allocations,
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
                RealizedAttribution(record=record, allocations=attributed)
            )
        return tuple(results)

    @staticmethod
    def _assignment_dates(events: list[PositionEvent]) -> dict[Instrument, list[date]]:
        result: dict[Instrument, list[date]] = {}
        for event in events:
            for change in event.changes:
                result.setdefault(change.instrument, []).append(event.occurred_at.date())
        return result

    @staticmethod
    def _has_trade_closure(trades: list[Trade], instrument: Instrument) -> bool:
        return any(
            leg.instrument == instrument and leg.position_effect == PositionEffect.CLOSE
            for trade in trades
            for leg in trade.legs
        )

    @classmethod
    def _economic_closed_date(
        cls, leg: Leg, assignment_dates: dict[Instrument, list[date]]
    ) -> date:
        trade_date = cls._leg_date(leg)
        dates = assignment_dates.get(leg.instrument, [])
        prior = [item for item in dates if item <= trade_date]
        if prior:
            return max(prior)

        # Schwab's Realized Gain/Loss closed date for ordinary option
        # transactions is the next business day (settlement), not the
        # Thinkorswim execution date. Stock dispositions remain same-day
        # for this report, while pending transactions are handled by the
        # settlement-aware importer before attribution.
        if isinstance(leg.instrument, OptionContract):
            return cls._next_business_day(trade_date)

        return trade_date

    @classmethod
    def _next_business_day(cls, value: date) -> date:
        candidate = value + timedelta(days=1)
        while candidate.weekday() >= 5 or cls._is_us_market_holiday(candidate):
            candidate += timedelta(days=1)
        return candidate

    @staticmethod
    def _is_us_market_holiday(value: date) -> bool:
        # NYSE-style full-day holidays relevant to settlement dates.
        # Kept local to the attribution boundary so the domain does not
        # acquire a dependency on a calendar package.
        if value.month == 1 and value.day == 1:
            return True
        if value.month == 1 and value.weekday() == 0 and 15 <= value.day <= 21:
            return True  # Martin Luther King Jr. Day
        if value.month == 2 and value.weekday() == 0 and 15 <= value.day <= 21:
            return True  # Presidents' Day
        if value.month == 6 and value.day == 19:
            return True  # Juneteenth
        if value.month == 7 and value.day == 4:
            return True  # Independence Day (observed handling below)
        if value.month == 9 and value.weekday() == 0 and 1 <= value.day <= 7:
            return True  # Labor Day
        if value.month == 11 and value.weekday() == 3 and 22 <= value.day <= 28:
            return True  # Thanksgiving
        if value.month == 12 and value.day == 25:
            return True  # Christmas

        # Good Friday: two days before Easter Sunday.
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

        # Saturday holidays are observed Friday; Sunday holidays Monday.
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

        A broker may aggregate several executions into one realized record.
        For example, Schwab reports one five-contract NVDA close while the
        trading history may contain separate four- and one-contract fills.
        Conversely, one execution may need to be split across multiple broker
        records.  Matching therefore operates on quantities rather than
        requiring one-to-one trade records.
        """
        remaining = record.quantity
        matched: list[LotAllocation] = []

        index = 0
        while index < len(activities) and remaining > 0:
            activity = activities[index]
            if (
                activity.closed_date != record.closed_date
                or activity.instrument != record.instrument
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
                        activity.allocations, take
                    ),
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
        return min(execution.executed_at for execution in leg.executions).date()

    @staticmethod
    def _leg_quantity(leg: Leg) -> Decimal:
        return sum(
            (abs(execution.quantity) for execution in leg.executions),
            Decimal("0"),
        )
