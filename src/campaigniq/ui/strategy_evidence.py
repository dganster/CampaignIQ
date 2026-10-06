"""Verify original order groups and read selected-end position state."""
from datetime import date
import calendar
from decimal import Decimal
import re
from pathlib import Path
from campaigniq.analytics.strategy_comparison import quantity
from campaigniq.domain.campaign_identity import underlying_symbol
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.importers.thinkorswim.trade_reader import ThinkorswimTradeReader
from campaigniq.importers.thinkorswim.trade_history_reader import ThinkorswimTradeHistoryReader
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader
from campaigniq.persistence.lot_book_store import _deserialize_instrument,load_lot_book_from_storage
from campaigniq.persistence.monthly_publication import is_month_published_in_storage
from campaigniq.ui.position_history_view import _source_matches_manifest

VERIFIED={'Published position journal','Verified retained trade history'}


def _matches(leg,row, *, action='Open / add'):
    q=quantity(leg)
    if not q or not leg.executions:return False
    price=sum((abs(e.quantity)*e.execution_price for e in leg.executions),Decimal('0'))/q
    sign=1 if leg.side is Side.BUY else -1
    return (row['action']==action and _deserialize_instrument(row['instrument'])==leg.instrument
            and row['occurred_at']==min(e.executed_at for e in leg.executions).isoformat()
            and Decimal(row['quantity_change'])==sign*q and row.get('price') is not None
            and Decimal(row['price'])==price)


def _covered(rows,trade):
    if len(trade.legs)!=1:return False
    leg=trade.legs[0]
    if not isinstance(leg.instrument,OptionContract) or leg.instrument.option_type is not OptionType.CALL or leg.side is not Side.SELL:return False
    at=min(e.executed_at for e in leg.executions).isoformat();latest={}
    for row in sorted(rows,key=lambda r:r['occurred_at']):
        instrument=_deserialize_instrument(row['instrument'])
        if row['occurred_at']>=at or underlying_symbol(instrument)!=leg.instrument.underlying or row.get('position_after') is None:
            continue
        latest[(instrument,row['campaign_id'])]=Decimal(row['position_after'])
    stock=sum((q for (i,cid),q in latest.items() if not isinstance(i,OptionContract)),Decimal('0'))
    committed=sum((-q*100 for (i,cid),q in latest.items() if isinstance(i,OptionContract) and i.option_type is OptionType.CALL and q<0),Decimal('0'))
    return stock-committed>=quantity(leg)*100


def load_strategy_entries(storage,root,evidence,campaigns):
    reader=ThinkorswimTradeReader(ThinkorswimSourceReader(),ThinkorswimTradeHistoryReader())
    cache={};result={}
    for campaign in campaigns:
        key=(campaign.underlying,campaign.campaign_id)
        identity=re.match(r'^(\d{4}-\d{2}):',campaign.campaign_id)
        if identity is None:
            result[key]=(None,False,'Legacy campaign identity cannot prove its original entry.');continue
        month=identity[1];start=date.fromisoformat(month+'-01');end=start.replace(day=calendar.monthrange(start.year,start.month)[1])
        rows=[r for r in evidence if r['period_start'][:7]==month and r.get('source') in VERIFIED]
        campaign_rows=[r for r in rows if r['campaign_id']==campaign.campaign_id and underlying_symbol(_deserialize_instrument(r['instrument']))==campaign.underlying]
        opens=[r for r in campaign_rows if r['action'] in {'Open / add','Roll: open'}]
        if not opens:
            result[key]=(None,False,'Original entry is absent from retained verified history.');continue
        earliest=min(r['occurred_at'] for r in opens)
        if any(r['action']=='Carried position' for r in campaign_rows):
            result[key]=(None,False,'Opening month contains a carried position; original entry is incomplete.');continue
        if month not in cache:
            try:
                if not storage.exists(f'{month}-import-provenance.json') or not _source_matches_manifest(storage,root,start,end):
                    raise ValueError('Source provenance unavailable or changed.')
                cache[month]=reader.read(Path(root)/f'Account Trade History {start:%B %Y}.csv',start=start,end=end)
            except (OSError,ValueError,KeyError,TypeError):
                cache[month]=None
        if cache[month] is None:
            result[key]=(None,False,'Archived original order could not be verified.');continue
        candidates=[]
        for trade in cache[month]:
            if not trade.legs or min(e.executed_at for l in trade.legs for e in l.executions).isoformat()!=earliest:
                continue
            if any(l.position_effect is not PositionEffect.OPEN for l in trade.legs):continue
            matched=[]
            for leg in trade.legs:
                matches=[r for r in campaign_rows if _matches(leg,r)]
                if len(matches)!=1:break
                matched.append(matches[0])
            if len(matched)==len(trade.legs) and len({id(r) for r in matched})==len(matched):
                candidates.append(trade)
        # Other campaign legs at the same entry timestamp must not be dropped.
        if len(candidates)!=1:
            result[key]=(None,False,'No unique complete original opening order matches the campaign.');continue
        trade=candidates[0]
        first_rows=[r for r in opens if r['occurred_at']==earliest]
        if any(not any(_matches(l,r) for l in trade.legs) for r in first_rows):
            result[key]=(None,False,'Entry includes additional unmatched legs.');continue
        covered=_covered(rows,trade)
        reason='Verified archived opening order and campaign journal legs.'
        if covered:reason+=' Retained stock balance covers this call after allowing for existing short calls.'
        result[key]=(trade,covered,reason)
    return result


def campaign_open_status(storage,period_end,campaigns):
    try:
        if not is_month_published_in_storage(storage,period_end=period_end):return {}
        saved=load_lot_book_from_storage(storage,f'{period_end:%Y-%m}-lot-book.json')
        if saved.period_end!=period_end:return {}
    except (OSError,ValueError,KeyError,TypeError):return {}
    active={(underlying_symbol(lot.instrument),lot.campaign_id) for i in saved.lot_book.instruments() for lot in saved.lot_book.lots(i)}
    uncertain_symbols = {underlying_symbol(lot.instrument) for i in saved.lot_book.instruments()
                         for lot in saved.lot_book.lots(i)
                         if not lot.campaign_id or not re.match(r'^\d{4}-\d{2}:',lot.campaign_id)}
    return {(c.underlying,c.campaign_id):True if (c.underlying,c.campaign_id) in active else
            None if c.underlying in uncertain_symbols else False
            for c in campaigns if re.match(r'^\d{4}-\d{2}:',c.campaign_id)}



def _verified_roll_execution_groups(storage, root, month_rows, campaign_rows, initial):
    """Verify complete same-contract roll execution groups against journal totals.

    No records are deduplicated and no nearby orders are paired. Each source
    group already contains its opening and closing legs. This returns only an
    observed opening position shape, not a claim about one original order.
    """
    from collections import defaultdict
    from campaigniq.domain.leg import Leg
    from campaigniq.domain.trade import Trade
    month=initial[0]['period_start'][:7]
    start=date.fromisoformat(month+'-01');end=start.replace(day=calendar.monthrange(start.year,start.month)[1])
    try:
        if not storage.exists(f'{month}-import-provenance.json') or not _source_matches_manifest(storage,root,start,end):
            return None
        reader=ThinkorswimTradeReader(ThinkorswimSourceReader(),ThinkorswimTradeHistoryReader())
        trades=reader.read(Path(root)/f'Account Trade History {start:%B %Y}.csv',start=start,end=end)
    except (OSError,ValueError,KeyError,TypeError):return None
    def leg_key(leg):
        return (leg.instrument,leg.side,leg.position_effect,min(e.executed_at for e in leg.executions).isoformat())
    def row_key(row):
        q=Decimal(row['quantity_change'])
        return (_deserialize_instrument(row['instrument']),Side.BUY if q>0 else Side.SELL,
                PositionEffect.OPEN if row['action']=='Roll: open' else PositionEffect.CLOSE,row['occurred_at'])
    initial_keys={row_key(r) for r in initial}
    candidates=[]
    for trade in trades:
        if {l.position_effect for l in trade.legs}!={PositionEffect.OPEN,PositionEffect.CLOSE}:continue
        opens=[l for l in trade.legs if l.position_effect is PositionEffect.OPEN]
        if opens and all(leg_key(l) in initial_keys for l in opens):
            candidates.append(trade)
    if len(candidates)<2:return None
    opening_legs=[l for t in candidates for l in t.legs if l.position_effect is PositionEffect.OPEN]
    if len({(l.instrument,l.side) for l in opening_legs})!=1:return None
    expected=defaultdict(lambda:[Decimal('0'),Decimal('0')])
    for trade in candidates:
        for leg in trade.legs:
            values=expected[leg_key(leg)]
            values[0]+=quantity(leg)
            values[1]+=sum((abs(e.quantity)*e.execution_price for e in leg.executions),Decimal('0'))
    observed=defaultdict(lambda:[Decimal('0'),Decimal('0')])
    for row in month_rows:
        if row['action'] not in {'Roll: open','Roll: close'} or row.get('price') is None:continue
        if row['action']=='Roll: open' and row['campaign_id']!=initial[0]['campaign_id']:continue
        key=row_key(row)
        if key in expected:
            q=abs(Decimal(row['quantity_change']))
            observed[key][0]+=q;observed[key][1]+=q*Decimal(row['price'])
    if observed!=expected:return None
    if not initial_keys.issubset({leg_key(l) for l in opening_legs}):return None
    instrument,side=next(iter({(l.instrument,l.side) for l in opening_legs}))
    leg=Leg(instrument,side,PositionEffect.OPEN,tuple(e for l in opening_legs for e in l.executions))
    return Trade((leg,)),len(candidates)


def _verified_open_execution_groups(storage,root,month_rows,initial):
    """Accept partial execution groups only with identical instrument ratios.

    Each archived group must itself contain the complete same opening shape.
    Separate nearby stock and option orders are never assembled into a strategy.
    Source quantities and price-weighted totals must equal the campaign journal.
    """
    from collections import defaultdict
    from campaigniq.domain.leg import Leg
    from campaigniq.domain.trade import Trade
    month=initial[0]['period_start'][:7]
    start=date.fromisoformat(month+'-01');end=start.replace(day=calendar.monthrange(start.year,start.month)[1])
    try:
        if not storage.exists(f'{month}-import-provenance.json') or not _source_matches_manifest(storage,root,start,end):return None
        reader=ThinkorswimTradeReader(ThinkorswimSourceReader(),ThinkorswimTradeHistoryReader())
        trades=reader.read(Path(root)/f'Account Trade History {start:%B %Y}.csv',start=start,end=end)
    except (OSError,ValueError,KeyError,TypeError):return None
    at=initial[0]['occurred_at'];cid=initial[0]['campaign_id']
    def key(leg):return (leg.instrument,leg.side)
    def row_key(row):return (_deserialize_instrument(row['instrument']),Side.BUY if Decimal(row['quantity_change'])>0 else Side.SELL)
    initial_keys={row_key(r) for r in initial}
    candidates=[];shapes=[]
    for trade in trades:
        if not trade.legs or any(l.position_effect is not PositionEffect.OPEN for l in trade.legs):continue
        if any(min(e.executed_at for e in l.executions).isoformat()!=at for l in trade.legs):continue
        quantities=defaultdict(Decimal)
        for l in trade.legs:quantities[key(l)]+=quantity(l)
        if set(quantities)!=initial_keys or any(q<=0 for q in quantities.values()):continue
        ordered=sorted(quantities,key=lambda k:(repr(k[0]),k[1].value));unit=quantities[ordered[0]]
        shapes.append(tuple((repr(k[0]),k[1].value,quantities[k]/unit) for k in ordered));candidates.append(trade)
    if len(candidates)<2 or len(set(shapes))!=1:return None
    expected=defaultdict(lambda:[Decimal('0'),Decimal('0')]);executions=defaultdict(list)
    for trade in candidates:
        for leg in trade.legs:
            k=key(leg);expected[k][0]+=quantity(leg)
            expected[k][1]+=sum((abs(e.quantity)*e.execution_price for e in leg.executions),Decimal('0'))
            executions[k].extend(leg.executions)
    observed=defaultdict(lambda:[Decimal('0'),Decimal('0')])
    for row in month_rows:
        if row['campaign_id']!=cid or row['occurred_at']!=at:continue
        if row['action']!='Open / add' or row.get('price') is None:return None
        k=row_key(row);q=abs(Decimal(row['quantity_change']))
        observed[k][0]+=q;observed[k][1]+=q*Decimal(row['price'])
    if observed!=expected:return None
    return Trade(tuple(Leg(i,side,PositionEffect.OPEN,tuple(executions[(i,side)])) for i,side in sorted(executions,key=lambda k:(repr(k[0]),k[1].value)))),len(candidates)

def load_observed_strategies(storage, root, evidence, campaigns, period_end, original_entries):
    """Keep the first available verified snapshot/order separate from original entry.

    Only explicit monthly carry snapshots describe positions. Close-only rows
    cannot reconstruct a strategy. Rolls require complete verified order groups.
    """
    from datetime import datetime
    from campaigniq.analytics.strategy_comparison import ObservedStrategy, classify_holdings, observed_order_label
    from campaigniq.ui.roll_evidence import load_campaign_rolls
    from campaigniq.domain.trade import Trade
    verified = [r for r in evidence if r.get('source') in VERIFIED
                and datetime.fromisoformat(r['occurred_at']).date() <= period_end]
    reader = ThinkorswimTradeReader(ThinkorswimSourceReader(), ThinkorswimTradeHistoryReader())
    orders = {}; roll_cache = {}; results = {}
    for campaign in campaigns:
        key = (campaign.underlying, campaign.campaign_id)
        raw_cid = campaign.campaign_id
        legacy_month = None
        if re.match(r'^\d{4}-\d{2}/', raw_cid):
            legacy_month, raw_cid = raw_cid.split('/', 1)
        rows = sorted((r for r in verified if r['campaign_id'] == raw_cid
                       and underlying_symbol(_deserialize_instrument(r['instrument'])) == campaign.underlying
                       and (legacy_month is None or r['period_start'][:7] == legacy_month)),
                      key=lambda r:r['occurred_at'])
        if not rows:
            results[key] = ObservedStrategy('Unclassified', 'Unavailable', None, 'No campaign-linked verified observation is retained.');continue
        first = rows[0];at = datetime.fromisoformat(first['occurred_at']);month = first['period_start'][:7]
        initial = [r for r in rows if r['occurred_at'] == first['occurred_at'] and r['period_start'] == first['period_start']]
        month_rows = [r for r in verified if r['period_start'][:7] == month]
        if all(r['action'] == 'Carried position' for r in initial):
            holdings = [(_deserialize_instrument(r['instrument']), Decimal(r['quantity_change'])) for r in initial]
            # Same timestamp is the retained opening snapshot, not nearby trades.
            snapshot = [r for r in month_rows if r['action']=='Carried position' and r['occurred_at']==first['occurred_at']]
            stock = sum((Decimal(r['quantity_change']) for r in snapshot if not isinstance(_deserialize_instrument(r['instrument']), OptionContract)
                         and underlying_symbol(_deserialize_instrument(r['instrument']))==campaign.underlying),Decimal('0'))
            calls = sum((-Decimal(r['quantity_change'])*100 for r in snapshot
                         if isinstance((i:=_deserialize_instrument(r['instrument'])),OptionContract)
                         and i.underlying==campaign.underlying and i.option_type is OptionType.CALL and Decimal(r['quantity_change'])<0),Decimal('0'))
            valid = all(r.get('position_after') is not None and Decimal(r['position_after'])==Decimal(r['quantity_change'])
                        and sum((Decimal(l['quantity']) for l in r['lots']),Decimal('0'))==abs(Decimal(r['quantity_change'])) for r in initial)
            label = classify_holdings(holdings,covered_call=stock>=calls and calls>0) if valid else 'Unclassified'
            reason = 'Simultaneous carried-position snapshot from verified campaign journal; original entry is not established.'
            if label=='Unclassified':reason+=' Snapshot is incomplete or outside supported position shapes.'
            results[key] = ObservedStrategy(label,'Carried-position snapshot',at,reason);continue
        if any(r['action'] not in {'Open / add','Roll: open'} for r in initial):
            results[key]=ObservedStrategy('Unclassified','Unavailable',at,'First retained event is not a complete position snapshot or opening order.');continue
        original, covered, reason = original_entries.get(key,(None,False,''))
        if original is not None and min(e.executed_at for l in original.legs for e in l.executions)==at:
            results[key]=ObservedStrategy(observed_order_label(original,covered_call=covered),'Verified opening order',at,reason);continue
        if all(r['action']=='Roll: open' for r in initial):
            if month not in roll_cache:
                selected=[r for r in month_rows if r['action']=='Roll: open']
                roll_cache[month]=load_campaign_rolls(storage,root,month_rows,selected)[0]
            candidates=[]
            for roll in roll_cache[month]:
                matched = [r for r in rows if any(_matches(l,r,action='Roll: open') for l in roll.open_legs)]
                if (matched and min(r['occurred_at'] for r in matched)==first['occurred_at']
                        and all(sum(_matches(l,r,action='Roll: open') for r in matched)==1 for l in roll.open_legs)
                        and len(matched)==len(roll.open_legs)
                        and all(any(_matches(l,r,action='Roll: open') for l in roll.open_legs) for r in initial)):
                    candidates.append(Trade(roll.open_legs))
            if len(candidates)==1:
                trade=candidates[0];covered=_covered(month_rows,trade)
                results[key]=ObservedStrategy(observed_order_label(trade,covered_call=covered),'Verified roll opening legs',at,
                    'Opening legs of one complete verified roll order match this campaign; the closing legs were also verified. This is an observed roll strategy, not proof of the original entry.');continue
            grouped = _verified_roll_execution_groups(storage,root,month_rows,rows,initial)
            if grouped is not None:
                trade,count=grouped
                results[key]=ObservedStrategy(observed_order_label(trade,covered_call=_covered(month_rows,trade)),
                    'Verified roll execution groups',at,
                    f'{count} complete same-contract source roll execution groups match all retained opening and closing quantities and prices. This establishes an observed position shape; no single-order identity or original entry is inferred.');continue
            results[key]=ObservedStrategy('Unclassified','Unavailable',at,'First roll cannot be linked to one complete verified order.');continue
        if month not in orders:
            start=date.fromisoformat(month+'-01');end=start.replace(day=calendar.monthrange(start.year,start.month)[1])
            try:
                if not storage.exists(f'{month}-import-provenance.json') or not _source_matches_manifest(storage,root,start,end):
                    raise ValueError('Source provenance unavailable.')
                orders[month]=reader.read(Path(root)/f'Account Trade History {start:%B %Y}.csv',start=start,end=end)
            except (OSError,ValueError,KeyError,TypeError):orders[month]=None
        candidates=[]
        for trade in orders[month] or ():
            if not trade.legs or any(l.position_effect is not PositionEffect.OPEN for l in trade.legs):continue
            if min(e.executed_at for l in trade.legs for e in l.executions)!=at:continue
            matches=[r for r in rows if any(_matches(l,r) for l in trade.legs)]
            if (all(sum(_matches(l,r) for r in matches)==1 for l in trade.legs)
                    and len(matches)==len(trade.legs) and all(any(_matches(l,r) for l in trade.legs) for r in initial)):
                candidates.append(trade)
        if len(candidates)==1:
            trade=candidates[0];covered=_covered(month_rows,trade)
            results[key]=ObservedStrategy(observed_order_label(trade,covered_call=covered),'Verified observed opening order',at,
                'Complete verified opening order matches the earliest retained campaign event; original entry identity remains unproven.')
        else:
            grouped = _verified_open_execution_groups(storage,root,month_rows,initial)
            if grouped is not None:
                trade,count=grouped
                results[key]=ObservedStrategy(observed_order_label(trade,covered_call=_covered(month_rows,trade)),
                    'Verified opening execution groups',at,
                    f'{count} complete opening execution groups have identical instrument ratios and match all retained campaign quantities and price-weighted totals. No separate orders were paired into a strategy.')
            else:
                results[key]=ObservedStrategy('Unclassified','Unavailable',at,'First observed opening order is not uniquely verified.')
    # Export actual retained first-event legs for the remaining review cases.
    # This adds audit detail without changing a classification or source row.
    from dataclasses import replace
    import json
    for key, result in tuple(results.items()):
        symbol,cid=key;raw_cid=cid;legacy_month=None
        if re.match(r'^\d{4}-\d{2}/',cid):legacy_month,raw_cid=cid.split('/',1)
        first_rows=[r for r in verified if result.observed_at is not None
                    and r['occurred_at']==result.observed_at.isoformat() and r['campaign_id']==raw_cid
                    and underlying_symbol(_deserialize_instrument(r['instrument']))==symbol
                    and (legacy_month is None or r['period_start'][:7]==legacy_month)]
        legs=[{'instrument':r['instrument'],'action':r['action'],'quantity_change':r['quantity_change'],
               'position_after':r.get('position_after'),'price':r.get('price'),
               'retained_lot_quantity':str(sum((Decimal(l['quantity']) for l in r['lots']),Decimal('0')))} for r in first_rows]
        results[key]=replace(result,observed_legs=json.dumps(legs,sort_keys=True))
    return results
