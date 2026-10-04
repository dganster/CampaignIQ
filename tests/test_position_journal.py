from datetime import date, datetime
from decimal import Decimal as D
from types import SimpleNamespace
import json

import pytest

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.lot_book_period_applier import LotBookPeriodApplier
from campaigniq.domain.position_effect import PositionEffect as Effect
from campaigniq.domain.position_event import PositionEvent, PositionChange
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.position_journal import capture_position_journal, serialize_position_journal, deserialize_position_journal

START, END = date(2026, 9, 1), date(2026, 9, 30)


def trade(symbol, side, effect, quantity, day, price="100"):
    return Trade((Leg(Instrument(symbol), side, effect,
                      (Execution(D(quantity), D(price), datetime(2026, 9, day, 10)),)),))


def imported(campaigns, *, opening=None, events=()):
    history = PositionHistory()
    for campaign in campaigns:
        for item in campaign.trades:
            history.add_trade(item)
    for event in events:
        history.add_event(event)
    opening = opening or LotBook()
    ending = LotBookPeriodApplier().apply(opening_lot_book=opening, position_history=history, campaigns=tuple(campaigns))
    return SimpleNamespace(opening_lot_book=opening, ending_lot_book=ending,
                           campaigns=tuple(campaigns), position_history=history)


def test_short_position_and_partial_close_are_signed_and_opening_is_preserved():
    result = imported([Campaign("2026-09:ABC:CAMP-000001", (
        trade("ABC", Side.SELL, Effect.OPEN, "2", 1),
        trade("ABC", Side.BUY, Effect.CLOSE, "1", 10),
        trade("ABC", Side.BUY, Effect.CLOSE, "1", 20)))])
    rows = capture_position_journal(result, START)
    assert [D(row["quantity_change"]) for row in rows] == [D(-2), D(1), D(1)]
    assert [D(row["position_after"]) for row in rows] == [D(-2), D(-1), D(0)]
    assert [row["action"] for row in rows] == ["Open / add", "Close / reduce", "Close / reduce"]
    assert result.opening_lot_book.instruments() == ()


def test_closing_order_cannot_take_another_campaigns_history():
    opening = LotBook()
    symbol = Instrument("ABC")
    opening.seed(Lot("older", symbol, D(-1), datetime(2026, 8, 1), None, campaign_id="old"))
    opening.seed(Lot("newer", symbol, D(-1), datetime(2026, 8, 20), None, campaign_id="new"))
    result = imported([Campaign("closing-order", (trade("ABC", Side.BUY, Effect.CLOSE, "2", 10),))], opening=opening)
    closes = [row for row in capture_position_journal(result, START) if row["action"] == "Close / reduce"]
    assert [(row["campaign_id"], row["quantity_change"], row["position_after"]) for row in closes] == [("old", "1", "0"), ("new", "1", "0")]
    assert opening.lots(symbol)[0].quantity == D(-1)


def test_roll_preserves_closed_lot_campaign_and_new_opening_campaign():
    closing = trade("ABC", Side.BUY, Effect.CLOSE, "1", 10)
    opening_trade = trade("ABC", Side.SELL, Effect.OPEN, "1", 10, "105")
    roll = Trade(closing.legs + opening_trade.legs)
    book = LotBook(); book.seed(Lot("old", Instrument("ABC"), D(-1), datetime(2026, 8, 1), None, campaign_id="before"))
    rows = capture_position_journal(imported([Campaign("after", (roll,))], opening=book), START)
    assert [(r["action"], r["campaign_id"]) for r in rows[1:]] == [("Roll: close", "before"), ("Roll: open", "after")]


def test_event_changes_are_retained_with_signed_balances():
    book = LotBook(); symbol = Instrument("ABC")
    book.seed(Lot("old", symbol, D(2), datetime(2026, 8, 1), None, campaign_id="old"))
    event = PositionEvent(PositionEventKind.EXPIRATION, (PositionChange(symbol, D(-2)),), datetime(2026, 9, 20))
    rows = capture_position_journal(imported([], opening=book, events=(event,)), START)
    assert rows[-1]["action"] == "Expiration"
    assert rows[-1]["quantity_change"] == "-2" and rows[-1]["position_after"] == "0"
    assert rows[-1]["campaign_id"] == "old"


def test_journal_round_trip_validates_reporting_period():
    result = imported([Campaign("one", (trade("ABC", Side.BUY, Effect.OPEN, "1", 1),))])
    text = serialize_position_journal(result, period_start=START, period_end=END)
    assert deserialize_position_journal(text, period_start=START, period_end=END) == capture_position_journal(result, START)
    data = json.loads(text); data["entries"][0]["occurred_at"] = "2026-10-01T10:00:00"
    with pytest.raises(ValueError, match="outside"):
        deserialize_position_journal(json.dumps(data), period_start=START, period_end=END)
    with pytest.raises(ValueError, match="period"):
        deserialize_position_journal(text, period_start=date(2026, 8, 1), period_end=END)


def test_divergent_journal_cannot_be_published():
    result = imported([Campaign("one", (trade("ABC", Side.BUY, Effect.OPEN, "1", 1),))])
    result.ending_lot_book = LotBook()
    with pytest.raises(ValueError, match="differs"):
        serialize_position_journal(result, period_start=START, period_end=END)


def test_carried_lots_are_one_starting_balance_per_campaign_and_instrument():
    book = LotBook(); symbol = Instrument("ABC")
    for number, day in enumerate((1, 20), 1):
        book.seed(Lot(f"lot-{number}", symbol, D(-1), datetime(2026, 8, day), None, campaign_id="same"))
    rows = capture_position_journal(imported([], opening=book), START)
    assert len(rows) == 1 and rows[0]["quantity_change"] == "-2" and rows[0]["position_after"] == "-2"
    assert len(rows[0]["opening_refs"]) == 2


def test_option_assignment_preserves_campaign_into_delivered_stock():
    from campaigniq.domain.option_contract import OptionContract
    from campaigniq.domain.option_type import OptionType
    option = OptionContract("ABC", date(2026, 9, 18), D(100), OptionType.PUT)
    book = LotBook(); book.seed(Lot("put", option, D(-1), datetime(2026, 8, 1), None, campaign_id="put-campaign"))
    event = PositionEvent(PositionEventKind.ASSIGNMENT,
                          (PositionChange(option, D(1)), PositionChange(Instrument("ABC"), D(100))), datetime(2026, 9, 18))
    rows = capture_position_journal(imported([], opening=book, events=(event,)), START)
    changes = [row for row in rows if row["action"] == "Assignment"]
    assert [(row["quantity_change"], row["position_after"]) for row in changes] == [("1", "0"), ("100", "100")]
    assert {row["campaign_id"] for row in changes} == {"put-campaign"}
