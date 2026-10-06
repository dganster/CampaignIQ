"""Gross cash flow and contract changes for an explicitly grouped option roll."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side


@dataclass(frozen=True, slots=True)
class RollAnalysis:
    occurred_at: datetime
    underlying: str
    close_legs: tuple
    open_legs: tuple
    close_cash_flow: Decimal
    open_cash_flow: Decimal
    strike_change: Decimal | None
    expiration_change_days: int | None
    contract_quantity_change: Decimal | None

    @property
    def net_cash_flow(self):
        return self.close_cash_flow + self.open_cash_flow


def leg_quantity(leg):
    return sum((abs(e.quantity) for e in leg.executions), Decimal('0'))


def leg_cash_flow(leg):
    if not isinstance(leg.instrument,OptionContract) or leg.instrument.underlying.startswith('/'):
        raise ValueError('Roll cash flow supports standard stock options only; futures multipliers are not inferred.')
    total = Decimal('0')
    for execution in leg.executions:
        if not execution.quantity.is_finite() or not execution.execution_price.is_finite() or execution.execution_price < 0:
            raise ValueError('Invalid roll execution evidence.')
        total += abs(execution.quantity) * execution.execution_price * Decimal('100')
    return total if leg.side is Side.SELL else -total


def analyze_roll(trade):
    closes=tuple(leg for leg in trade.legs if leg.position_effect is PositionEffect.CLOSE)
    opens=tuple(leg for leg in trade.legs if leg.position_effect is PositionEffect.OPEN)
    if not closes or not opens or len(closes)+len(opens)!=len(trade.legs):
        raise ValueError('A roll requires explicitly grouped closing and opening legs.')
    if not all(isinstance(leg.instrument,OptionContract) for leg in trade.legs):
        raise ValueError('Mixed stock/option orders are not treated as option rolls.')
    symbols={leg.instrument.underlying for leg in trade.legs}
    if len(symbols)!=1:
        raise ValueError('A roll cannot combine different underlyings.')
    strike=days=quantity=None
    if len(closes)==len(opens)==1 and closes[0].instrument.option_type==opens[0].instrument.option_type:
        strike=opens[0].instrument.strike-closes[0].instrument.strike
        days=(opens[0].instrument.expiration-closes[0].instrument.expiration).days
        quantity=leg_quantity(opens[0])-leg_quantity(closes[0])
    return RollAnalysis(min(e.executed_at for leg in trade.legs for e in leg.executions),next(iter(symbols)),closes,opens,
                        sum((leg_cash_flow(leg) for leg in closes),Decimal('0')),
                        sum((leg_cash_flow(leg) for leg in opens),Decimal('0')),strike,days,quantity)
