"""Evidence-based entry labels and selected-period realized strategy summaries."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import median
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.side import Side
from campaigniq.domain.position_effect import PositionEffect

ZERO=Decimal('0')


def quantity(leg):
    if isinstance(leg, PositionShapeLeg):
        return leg.observed_quantity
    return sum((abs(e.quantity) for e in leg.executions),ZERO)


def classify_entry(trade, *, covered_call=False):
    """Classify a complete verified opening order, never nearby executions."""
    return _classify_legs(trade.legs, covered_call=covered_call)


def _classify_legs(legs, *, covered_call=False):
    if not legs or any(l.position_effect is not PositionEffect.OPEN for l in legs):
        return 'Unclassified'
    symbols={getattr(l.instrument,'underlying',None) or l.instrument.symbol for l in legs}
    if len(symbols)!=1 or next(iter(symbols)).startswith('/') or any(quantity(l)<=0 for l in legs):
        return 'Unclassified'
    options=[l for l in legs if isinstance(l.instrument,OptionContract)]
    stocks=[l for l in legs if not isinstance(l.instrument,OptionContract)]
    if stocks and not options:
        return 'Stock entry' if all(l.side is Side.BUY for l in stocks) else 'Short stock entry'
    if stocks:
        if (len(stocks)==len(options)==1 and stocks[0].side is Side.BUY
                and options[0].side is Side.SELL and options[0].instrument.option_type is OptionType.CALL
                and quantity(stocks[0])==quantity(options[0])*100):
            return 'Covered call'
        return 'Unclassified'
    if any(quantity(l)!=quantity(l).to_integral_value() for l in options):
        return 'Unclassified'
    if len(options)==1:
        leg=options[0];kind=leg.instrument.option_type.value.lower()
        if leg.side is Side.BUY:
            return 'Long '+kind
        if kind=='call':
            return 'Covered call' if covered_call else 'Short call; coverage unverified'
        return 'Short put'
    if len({l.instrument.expiration for l in options})!=1 or len({quantity(l) for l in options})!=1:
        return 'Unclassified'
    if len(options)==2:
        a,b=options
        if (a.instrument.option_type is b.instrument.option_type and a.side is not b.side
                and a.instrument.strike!=b.instrument.strike):
            long = a if a.side is Side.BUY else b
            short = b if a.side is Side.BUY else a
            debit = (long.instrument.strike < short.instrument.strike) if a.instrument.option_type is OptionType.CALL else (long.instrument.strike > short.instrument.strike)
            return a.instrument.option_type.value.title()+(' debit vertical' if debit else ' credit vertical')
        puts=[l for l in options if l.instrument.option_type is OptionType.PUT]
        calls=[l for l in options if l.instrument.option_type is OptionType.CALL]
        if len(puts)==len(calls)==1 and all(l.side is Side.SELL for l in options):
            p,c=puts[0].instrument.strike,calls[0].instrument.strike
            if p==c:return 'Short straddle'
            if p<c:return 'Short strangle'
            if p>c:return 'Inverted short strangle'
    if len(options)==4:
        puts=sorted((l for l in options if l.instrument.option_type is OptionType.PUT),key=lambda l:l.instrument.strike)
        calls=sorted((l for l in options if l.instrument.option_type is OptionType.CALL),key=lambda l:l.instrument.strike)
        if (len(puts)==len(calls)==2 and puts[0].side is Side.BUY and puts[1].side is Side.SELL
                and calls[0].side is Side.SELL and calls[1].side is Side.BUY
                and puts[0].instrument.strike<puts[1].instrument.strike<calls[0].instrument.strike<calls[1].instrument.strike):
            return 'Iron condor'
    return 'Unclassified'


@dataclass(frozen=True, slots=True)
class PositionShapeLeg:
    instrument: object
    side: Side
    observed_quantity: Decimal
    position_effect: PositionEffect = PositionEffect.OPEN


@dataclass(frozen=True, slots=True)
class ObservedStrategy:
    strategy: str
    basis: str
    observed_at: datetime | None
    reason: str
    observed_legs: str = ''


def observed_order_label(trade, *, covered_call=False):
    label = classify_entry(trade, covered_call=covered_call)
    return {'Stock entry': 'Stock position', 'Short stock entry': 'Short stock position'}.get(label, label)


def classify_holdings(holdings, *, covered_call=False):
    """Describe a simultaneous retained position snapshot, not an opening trade."""
    totals = {}
    for instrument, signed_quantity in holdings:
        totals[instrument] = totals.get(instrument, ZERO) + signed_quantity
    legs = tuple(PositionShapeLeg(i, Side.BUY if q > ZERO else Side.SELL, abs(q))
                 for i, q in totals.items() if q)
    # Extra long shares still cover a single short call position.
    options = [l for l in legs if isinstance(l.instrument, OptionContract)]
    stocks = [l for l in legs if not isinstance(l.instrument, OptionContract)]
    if (len(options) == len(stocks) == 1 and options[0].side is Side.SELL
            and options[0].instrument.option_type is OptionType.CALL
            and stocks[0].side is Side.BUY and quantity(stocks[0]) >= quantity(options[0])*100
            and stocks[0].instrument.symbol == options[0].instrument.underlying):
        return 'Covered call'
    label = _classify_legs(legs, covered_call=covered_call)
    return {'Stock entry': 'Stock position', 'Short stock entry': 'Short stock position'}.get(label, label)


def compare_strategies(campaigns, entry_evidence, open_status, *, observed_evidence=None):
    """Group realized P&L; win rate uses only confirmed closed campaigns."""
    details=[]
    for campaign in campaigns:
        trade,covered,reason=entry_evidence.get((campaign.underlying,campaign.campaign_id),(None,False,'Original entry order is not verified.'))
        label=classify_entry(trade,covered_call=covered) if trade is not None else 'Unclassified'
        if trade is not None and label == 'Unclassified':
            reason += ' Opening shape is outside the supported strategy definitions.'
        observed = observed_evidence.get((campaign.underlying, campaign.campaign_id)) if observed_evidence is not None else None
        basis = 'Original entry' if label != 'Unclassified' else 'Original entry unavailable'
        observed_at = min(e.executed_at for l in trade.legs for e in l.executions) if trade is not None and trade.legs else None
        if observed_evidence is not None:
            label = observed.strategy if observed else 'Unclassified'
            reason = observed.reason if observed else 'No complete verified observation is available.'
            basis = observed.basis if observed else 'Unavailable'
            observed_at = observed.observed_at if observed else None
            if label == 'Unclassified' and basis != 'Unavailable' and 'supported' not in reason:
                reason += ' Observed shape is outside supported strategy definitions.'
        details.append(dict(campaign=campaign,strategy=label,evidence=reason,basis=basis,observed_at=observed_at,
                            observed_legs=observed.observed_legs if observed else '',
                            open=open_status.get((campaign.underlying,campaign.campaign_id))))
    groups=[]
    for label in sorted({r['strategy'] for r in details}):
        rows=[r for r in details if r['strategy']==label]
        values=[r['campaign'].realized_pnl for r in rows]
        closed=[r['campaign'].realized_pnl for r in rows if r['open'] is False]
        wins=sum(v>0 for v in closed)
        groups.append(dict(strategy=label,campaigns=len(rows),open=sum(r['open'] is True for r in rows),
                           unknown=sum(r['open'] is None for r in rows),closed=len(closed),
                           win_rate=Decimal(wins)/len(closed) if closed else None,
                           realized=sum(values,ZERO),average=sum(values,ZERO)/len(values),median=median(values)))
    return tuple(groups),tuple(details)


def unclassified_review(details):
    """Summarize the actual retained classification explanations, without relabeling."""
    totals = {}
    rows = []
    for item in details:
        if item['strategy'] != 'Unclassified':
            continue
        campaign = item['campaign']
        reason = item['evidence']
        group = totals.setdefault(reason, {'Reason': reason, 'Campaigns': 0, 'Realized P&L': ZERO})
        group['Campaigns'] += 1
        group['Realized P&L'] += campaign.realized_pnl
        rows.append({'Underlying': campaign.underlying, 'Campaign': campaign.campaign_id,
                     'Reporting detail': campaign.drilldown_id, 'Realized P&L': campaign.realized_pnl,
                     'Position status': 'Open' if item['open'] is True else 'Closed' if item['open'] is False else 'Unavailable',
                     'Reason': reason, 'First observation journal legs': item.get('observed_legs', ''),
                     'Classification basis': item.get('basis', 'Original entry'),
                     'Observation date': item.get('observed_at').isoformat() if item.get('observed_at') else ''})
    return (tuple(sorted(totals.values(), key=lambda r: (-r['Campaigns'], r['Reason']))),
            tuple(sorted(rows, key=lambda r: (r['Reason'], r['Underlying'], r['Campaign']))))
