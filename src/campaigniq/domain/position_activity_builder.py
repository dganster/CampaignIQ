"""Build position activities from compatible trades."""

from campaigniq.domain.position_activity import PositionActivity
from campaigniq.domain.position_activity_kind import PositionActivityKind
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.trade import Trade


class PositionActivityBuilder:
    """Group compatible trades into position activities."""

    def build(self, trades: list[Trade]) -> list[PositionActivity]:
        """Build position activities from trades."""

        activities: list[PositionActivity] = []

        for trade in trades:
            matched = False

            for index, activity in enumerate(activities):
                if self._compatible(activity, trade):
                    activities[index] = PositionActivity(
                        trades=activity.trades + (trade,),
                        kind=activity.kind,
                    )
                    matched = True
                    break

            if not matched:
                activities.append(
                    PositionActivity(
                        trades=(trade,),
                        kind=self._kind_for(trade),
                    )
                )

        return activities

    def _compatible(
        self,
        activity: PositionActivity,
        trade: Trade,
    ) -> bool:
        """Return whether a trade belongs to an existing activity."""

        reference = activity.trades[0]

        if len(reference.legs) != len(trade.legs):
            return False

        for reference_leg, trade_leg in zip(
            reference.legs,
            trade.legs,
        ):
            if reference_leg.instrument != trade_leg.instrument:
                return False

            if reference_leg.side != trade_leg.side:
                return False

            if reference_leg.position_effect != trade_leg.position_effect:
                return False

        return True

    def _kind_for(self, trade: Trade) -> PositionActivityKind:
        """Determine the initial activity kind for a trade."""

        effects = {
            leg.position_effect
            for leg in trade.legs
        }

        if effects == {PositionEffect.OPEN}:
            return PositionActivityKind.OPEN

        if effects == {PositionEffect.CLOSE}:
            return PositionActivityKind.CLOSE

        return PositionActivityKind.ADJUST
