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


def _matches(leg,row):
    q=quantity(leg)
    if not q or not leg.executions:return False
    price=sum((abs(e.quantity)*e.execution_price for e in leg.executions),Decimal('0'))/q
    sign=1 if leg.side is Side.BUY else -1
    return (row['action']=='Open / add' and _deserialize_instrument(row['instrument'])==leg.instrument
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
