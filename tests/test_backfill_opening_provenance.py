"""Opening evidence must not turn an unknown origin into a campaign identity."""
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal as D
from types import SimpleNamespace
import copy
import pytest

from campaigniq.historical_backfill_opening_provenance import trace_opening_provenance
from campaigniq.domain.campaign import Campaign
from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.lot_book_period_applier import LotBookPeriodApplier
from campaigniq.domain.position_effect import PositionEffect as Effect
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind as Kind
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.lot_book_store import serialize_lot_book

STOCK = Instrument('ADBE')
PUT = OptionContract('ADBE', date(2025,2,21), D(500), OptionType.PUT)
DOCS = [dict(id='statement', role='schwab_brokerage_statement', sha256='a'*64),
        dict(id='trades', role='thinkorswim_trade_history', sha256='b'*64)]


def seed(*lots):
    book=LotBook()
    for lot in lots: book.seed(lot)
    return book


def snapshot(lid='opening-shares', instrument=STOCK, qty='500', cid=None):
    return Lot(lid,instrument,D(qty),datetime(2024,12,31,23,59,59),None,'SCHWAB_POSITION_SNAPSHOT',cid)


def leg(instrument=PUT,qty='10',side=Side.SELL,effect=Effect.OPEN,month=1):
    ex=Execution(D(qty),D('1'),datetime(2025,month,22,9,11,27))
    cls=OptionLeg if isinstance(instrument,OptionContract) else InstrumentLeg
    return cls(instrument,side,effect,(ex,),broker_strategy='SINGLE') if cls is OptionLeg else cls(instrument,side,effect,(ex,))


def result(book=None,trades=(),events=(),cid='SOURCE'):
    book=book if book is not None else LotBook()
    history=PositionHistory()
    for t in trades: history.add_trade(t)
    for e in events: history.add_event(e)
    campaigns=(Campaign(cid,tuple(trades)),) if trades else ()
    end=LotBookPeriodApplier().apply(opening_lot_book=book,position_history=history,campaigns=campaigns)
    return SimpleNamespace(opening_lot_book=book,ending_lot_book=end,position_history=history,campaigns=campaigns)


def trace(r,month=1,previous=None,documents=DOCS):
    import calendar
    return trace_opening_provenance(r,period_start=date(2025,month,1),period_end=date(2025,month,calendar.monthrange(2025,month)[1]),documents=documents,previous=previous)


def test_snapshot_observation_is_not_acquisition_or_campaign_start():
    r=result(seed(snapshot()))
    before=serialize_lot_book(period_end=date(2024,12,31),lot_book=r.opening_lot_book)
    p=trace(r); row=p['opening'][0]
    assert row['origin_kind']=='opening_balance'
    assert row['actual_campaign_start'] is None and row['acquisition_date'] is None
    assert row['basis_total'] is None and row['identity']['campaign_id'] is None
    assert not row['campaign_identity_verified'] and not row['observed_open_verified']
    assert not p['campaign_identities_assigned'] and not p['publication_authorized']
    assert before==serialize_lot_book(period_end=date(2024,12,31),lot_book=r.opening_lot_book)


def test_adbe_assignment_preserves_put_origin_and_separates_original_shares():
    book=seed(snapshot(),snapshot('opening-put',PUT,'-5'))
    event=PositionEvent(occurred_at=datetime(2025,1,8),kind=Kind.ASSIGNMENT,
        changes=(PositionChange(PUT,D(5)),PositionChange(STOCK,D(500))))
    r=result(book,events=(event,));p=trace(r)
    assigned=p['observed_origins'][0]
    assert assigned['origin_kind']=='observed_assignment'
    assert assigned['parent_lot_ids']==['opening-put']
    assert assigned['parent_origins'][0]['identity']['campaign_id'] is None
    assert assigned['actual_campaign_start'] is None
    assert assigned['lot_id']!='opening-shares'
    assert len(p['closing'])==2
    assert all(x['identity']['campaign_id'] is None for x in p['closing'].values())


def test_actual_opening_retains_exact_source_receipts_without_claiming_campaign_start():
    p=trace(result(trades=(Trade((leg(),)),)))
    row=p['observed_origins'][0]
    assert row['observed_open_verified'] and row['origin_kind']=='observed_open'
    assert row['observed_at']=='2025-01-22T09:11:27'
    assert row['actual_campaign_start'] is None
    assert row['source_documents']==DOCS and row['source_item_ordinal']==1


@pytest.mark.parametrize('docs',[[],DOCS[:1],DOCS[1:]])
def test_missing_source_roles_cannot_verify_an_opening(docs):
    p=trace(result(trades=(Trade((leg(),)),)),documents=docs)
    assert not p['observed_origins'][0]['observed_open_verified']


def test_roll_is_not_mistaken_for_a_new_campaign_start():
    book=seed(snapshot('prior-put',PUT,'-10','PRIOR'))
    following=replace(PUT,expiration=date(2025,4,17))
    roll=Trade((leg(following),leg(PUT,side=Side.BUY,effect=Effect.CLOSE)))
    p=trace(result(book,trades=(roll,)))
    row=p['observed_origins'][0]
    assert row['origin_kind']=='observed_trade_with_prior_inventory'
    assert row['parent_lot_ids']==['prior-put']
    assert row['parent_origins'][0]['origin_kind']=='opening_balance'
    assert not row['observed_open_verified'] and row['actual_campaign_start'] is None


def test_receipts_survive_exact_carry_without_mutating_previous_report():
    r=result(trades=(Trade((leg(),)),));p=trace(r);before=copy.deepcopy(p)
    q=trace(result(r.ending_lot_book),month=2,previous=p['closing'])
    assert q['opening'][0]['observed_open_verified']
    assert q['opening'][0]['observed_at']=='2025-01-22T09:11:27'
    assert p==before


@pytest.mark.parametrize('field,value',[
    ('campaign_id','CHANGED'),('opened_at',datetime(2024,1,1)),('basis_source','OTHER')])
def test_changed_identity_does_not_inherit_receipt(field,value):
    r=result(trades=(Trade((leg(),)),));p=trace(r)
    lot=next(iter(r.ending_lot_book.lots(PUT)))
    changed=seed(replace(lot,**{field:value}))
    q=trace(result(changed),month=2,previous=p['closing'])
    assert q['opening'][0]['origin_kind']=='changed_carried_identity'
    assert not q['opening'][0]['observed_open_verified']


@pytest.mark.parametrize('qty',['-11','10'])
def test_increased_or_reversed_quantity_cannot_reuse_origin(qty):
    r=result(trades=(Trade((leg(),)),));p=trace(r)
    lot=r.ending_lot_book.lots(PUT)[0]
    q=trace(result(seed(replace(lot,quantity=D(qty)))),month=2,previous=p['closing'])
    assert not q['opening'][0]['observed_open_verified']


def test_partial_closure_retains_separate_opening_origin():
    r=result(trades=(Trade((leg(),)),));p=trace(r)
    close=Trade((leg(qty='5',side=Side.BUY,effect=Effect.CLOSE,month=2),))
    q=trace(result(r.ending_lot_book,trades=(close,)),month=2,previous=p['closing'])
    assert q['opening'][0]['observed_open_verified']
    assert list(q['closing'].values())[0]['quantity']=='-5'


def test_ending_state_mismatch_is_blocking():
    r=result(trades=(Trade((leg(),)),));r.ending_lot_book=LotBook()
    with pytest.raises(ValueError,match='differs'):trace(r)


def test_unknown_trade_owner_is_blocking():
    r=result(trades=(Trade((leg(),)),));r.campaigns=()
    with pytest.raises(ValueError,match='Unowned'):trace(r)


@pytest.mark.parametrize('checksum',['bad','z'*64])
def test_invalid_evidence_hash_is_rejected(checksum):
    docs=copy.deepcopy(DOCS);docs[0]['sha256']=checksum
    with pytest.raises(ValueError,match='checksum'):trace(result(),documents=docs)
