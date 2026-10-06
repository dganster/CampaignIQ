"""Link original order groups to verified campaign journal rows without guessing rolls."""
import calendar
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from campaigniq.analytics.roll_analysis import analyze_roll, leg_quantity
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
from campaigniq.importers.thinkorswim.trade_reader import ThinkorswimTradeReader
from campaigniq.importers.thinkorswim.trade_history_reader import ThinkorswimTradeHistoryReader
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader
from campaigniq.persistence.lot_book_store import _deserialize_instrument
from campaigniq.ui.position_history_view import _source_matches_manifest


def load_campaign_rolls(storage, root, evidence, selected_entries):
    selected_ids={id(row) for row in selected_entries}
    selected_rolls=[row for row in selected_entries if row['action'] in {'Roll: open','Roll: close'}]
    months=sorted({row['period_start'][:7] for row in selected_rolls})
    reader=ThinkorswimTradeReader(ThinkorswimSourceReader(),ThinkorswimTradeHistoryReader())
    accepted=[];warnings=[];matched=set()
    for month in months:
        start=date.fromisoformat(month+'-01');end=start.replace(day=calendar.monthrange(start.year,start.month)[1])
        month_rows=[row for row in evidence if row['period_start'][:7]==month]
        try:
            if not storage.exists(f'{end:%Y-%m}-import-provenance.json') or not _source_matches_manifest(storage,root,start,end):
                raise ValueError('The archived source is missing verified provenance or has changed.')
            trades=reader.read(Path(root)/f'Account Trade History {start:%B %Y}.csv',start=start,end=end)
        except (ValueError,KeyError,OSError,TypeError) as exc:
            warnings.append(f'{month}: roll order evidence unavailable: {exc}')
            continue
        candidates=[]
        for trade in trades:
            if {leg.position_effect for leg in trade.legs}!={PositionEffect.OPEN,PositionEffect.CLOSE}:
                continue
            linked=[];valid=True
            for leg in trade.legs:
                quantity=leg_quantity(leg)
                if quantity==0:
                    valid=False;break
                price=sum((abs(e.quantity)*e.execution_price for e in leg.executions),Decimal('0'))/quantity
                at=min(e.executed_at for e in leg.executions).isoformat()
                action='Roll: open' if leg.position_effect is PositionEffect.OPEN else 'Roll: close'
                sign=Decimal('1') if leg.side is Side.BUY else Decimal('-1')
                rows=[row for row in month_rows if row['action']==action and row['occurred_at']==at
                      and _deserialize_instrument(row['instrument'])==leg.instrument and row['price'] is not None
                      and Decimal(row['price'])==price and Decimal(row['quantity_change'])*sign>0]
                if sum((abs(Decimal(row['quantity_change'])) for row in rows),Decimal('0'))!=quantity:
                    valid=False;break
                linked.extend(rows)
            if not valid or not selected_ids.intersection(id(row) for row in linked):
                continue
            try:
                roll=analyze_roll(trade)
            except ValueError as exc:
                warnings.append(f'{month}: {exc}')
                continue
            candidates.append((roll,{id(row) for row in linked}))
        uses=Counter(key for _,keys in candidates for key in keys)
        for roll,keys in candidates:
            if any(uses[key]>1 for key in keys):
                warnings.append(f'{month}: multiple source orders match the same journal legs; those rolls are omitted.')
                continue
            accepted.append(roll);matched.update(keys)
    missing=sum(id(row) not in matched for row in selected_rolls)
    if missing:
        warnings.append(f'{missing} retained roll leg(s) could not be linked to a complete, unique source order. No credit or debit is inferred for them.')
    return tuple(sorted(accepted,key=lambda r:r.occurred_at)),tuple(dict.fromkeys(warnings))


def closed_side_from_journal(attribution, entries):
    """Require exact consumed lots, campaign ancestry, quantity, and allowed broker dates."""
    expected=Counter()
    for a in attribution.allocations:
        if a.campaign_id is None:
            return 'Unavailable'
        cid=a.campaign_id.split('/',1)[1] if a.campaign_id[:7].count('-')==1 and '/' in a.campaign_id else a.campaign_id
        expected[(a.lot_id,cid)]+=a.quantity
    record=attribution.record;observed=Counter();signs=set()
    for row in entries:
        if row.get('source') not in {'Published position journal','Verified retained trade history'}:
            continue
        if row['action'] not in {'Close / reduce','Roll: close','Assignment','Exercise','Expiration'}:
            continue
        if _deserialize_instrument(row['instrument'])!=record.instrument:
            continue
        day=datetime.fromisoformat(row['occurred_at']).date()
        valid_date=day==record.closed_date
        if isinstance(record.instrument,OptionContract):
            valid_date=valid_date or RealizedLotAttributor._next_business_day(day)==record.closed_date
        if row['action'] in {'Assignment','Exercise'}:
            valid_date=valid_date or RealizedLotAttributor._next_business_day(record.closed_date)==day
        if not valid_date:
            continue
        for lot in row['lots']:
            key=(lot['lot_id'],row['campaign_id'])
            if key in expected:
                observed[key]+=Decimal(lot['quantity'])
                signs.add(Decimal(row['quantity_change']).compare(Decimal('0')))
    if observed!=expected or len(signs)!=1:
        return 'Unavailable'
    return 'Short' if signs=={Decimal('1')} else 'Long' if signs=={Decimal('-1')} else 'Unavailable'
