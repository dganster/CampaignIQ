"""Evidence-based entry labels and selected-period realized strategy summaries."""
from decimal import Decimal
from statistics import median
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.side import Side
from campaigniq.domain.position_effect import PositionEffect

ZERO=Decimal('0')


def quantity(leg):
    return sum((abs(e.quantity) for e in leg.executions),ZERO)


def classify_entry(trade, *, covered_call=False):
    """Classify a complete verified opening order, never nearby executions."""
    legs=trade.legs
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
    if len(options)==4:
        puts=sorted((l for l in options if l.instrument.option_type is OptionType.PUT),key=lambda l:l.instrument.strike)
        calls=sorted((l for l in options if l.instrument.option_type is OptionType.CALL),key=lambda l:l.instrument.strike)
        if (len(puts)==len(calls)==2 and puts[0].side is Side.BUY and puts[1].side is Side.SELL
                and calls[0].side is Side.SELL and calls[1].side is Side.BUY
                and puts[0].instrument.strike<puts[1].instrument.strike<calls[0].instrument.strike<calls[1].instrument.strike):
            return 'Iron condor'
    return 'Unclassified'


def compare_strategies(campaigns, entry_evidence, open_status):
    """Group realized P&L; win rate uses only confirmed closed campaigns."""
    details=[]
    for campaign in campaigns:
        trade,covered,reason=entry_evidence.get((campaign.underlying,campaign.campaign_id),(None,False,'Original entry order is not verified.'))
        label=classify_entry(trade,covered_call=covered) if trade is not None else 'Unclassified'
        if trade is not None and label == 'Unclassified':
            reason += ' Opening shape is outside the supported strategy definitions.'
        details.append(dict(campaign=campaign,strategy=label,evidence=reason,
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
