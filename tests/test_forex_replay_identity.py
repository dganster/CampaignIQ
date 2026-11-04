import json
from datetime import date
import pytest
from tests.test_lot_book_forex_identity import book
from campaigniq.domain.value_objects.forex_pair import ForexPair
from campaigniq.historical_backfill_replay import _book
from campaigniq.persistence.lot_book_store import serialize_lot_book

END = date(2026, 9, 30)

def payload():
    return json.loads(serialize_lot_book(period_end=END, lot_book=book(ForexPair("USD", "JPY"))))

def test_replay_accepts_explicit_forex_and_preserves_carried_evidence():
    source = book(ForexPair("USD", "JPY"))
    raw = serialize_lot_book(period_end=END, lot_book=source)
    assert _book(raw, END)._lots == source._lots

@pytest.mark.parametrize("change", [
    {"symbol": "EUR/JPY"}, {"base_currency": "usd"},
    {"quote_currency": None}, {"extra": "invalid"}, {"type": "unknown"},
])
def test_replay_rejects_corrupt_forex_descriptors(change):
    body = payload()
    body["lots"][0]["instrument"].update(change)
    with pytest.raises((ValueError, TypeError)):
        _book(json.dumps(body), END)
