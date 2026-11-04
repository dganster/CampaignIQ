from datetime import date,datetime
from decimal import Decimal as D
import json
import pytest
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.value_objects.forex_pair import ForexPair
from campaigniq.persistence.lot_book_store import serialize_lot_book,deserialize_lot_book


def book(instrument,quantity='-50000'):
    result=LotBook();result.seed(Lot(lot_id='carried-lot',instrument=instrument,quantity=D(quantity),
        opened_at=datetime(2026,9,10,10),basis_total=D('333.50'),basis_source='retained-evidence',campaign_id='retained-campaign'))
    return result


@pytest.mark.parametrize('symbol',['USD/JPY','EUR/USD','USD/CAD','GBP/USD','AUD/USD','USD/MXN'])
def test_new_forex_state_preserves_type_and_complete_lot_evidence(symbol):
    original=book(ForexPair.from_symbol(symbol))
    raw=serialize_lot_book(period_end=date(2026,9,30),lot_book=original)
    assert json.loads(raw)['lots'][0]['instrument']['type']=='forex_pair'
    loaded=deserialize_lot_book(raw)
    assert loaded.lot_book._lots==original._lots and loaded.period_end==date(2026,9,30)
    assert serialize_lot_book(period_end=loaded.period_end,lot_book=loaded.lot_book)==raw


def test_legacy_usdjpy_short_closes_instead_of_creating_two_opposite_instruments():
    raw=serialize_lot_book(period_end=date(2026,9,30),lot_book=book(Instrument('USD/JPY')))
    retained=raw.encode();loaded=deserialize_lot_book(raw).lot_book
    allocations=loaded.apply_signed_change(instrument=ForexPair('USD','JPY'),quantity=D(50000),
        occurred_at=datetime(2026,10,15,10),campaign_id='current-close')
    assert allocations and allocations[0].lot_id=='carried-lot'
    assert all(not rows for rows in loaded._lots.values())
    assert raw.encode()==retained # Reading does not rewrite legacy bytes.


def test_partial_close_retains_original_lot_campaign_and_basis():
    raw=serialize_lot_book(period_end=date(2026,9,30),lot_book=book(Instrument('USD/JPY')))
    loaded=deserialize_lot_book(raw).lot_book
    loaded.apply_signed_change(instrument=ForexPair('USD','JPY'),quantity=D(20000),occurred_at=datetime(2026,10,1))
    lot=loaded.lots(ForexPair('USD','JPY'))[0]
    assert lot.quantity==D(-30000) and lot.campaign_id=='retained-campaign' and lot.lot_id=='carried-lot'
    assert lot.basis_total==D('200.10') and lot.basis_source=='retained-evidence'


@pytest.mark.parametrize('symbol',['BTC/USD','ETH/USD','IBM','ABC/DEF','usd/jpy','USD/USD','BRK/B','/ESZ26'])
def test_unclassified_symbols_remain_generic(symbol):
    original=book(Instrument(symbol));raw=serialize_lot_book(period_end=date(2026,9,30),lot_book=original)
    loaded=deserialize_lot_book(raw).lot_book
    assert loaded._lots==original._lots
    assert type(next(iter(loaded._lots))) is Instrument


@pytest.mark.parametrize('change',[{'symbol':'EUR/JPY'},{'base_currency':'eur'},{'quote_currency':'USD'},
    {'base_currency':None},{'extra':'unreviewed'}])
def test_corrupt_explicit_forex_type_is_rejected(change):
    body=json.loads(serialize_lot_book(period_end=date(2026,9,30),lot_book=book(ForexPair('USD','JPY'))))
    body['lots'][0]['instrument'].update(change)
    with pytest.raises(ValueError):deserialize_lot_book(json.dumps(body))
