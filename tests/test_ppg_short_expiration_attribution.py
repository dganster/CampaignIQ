"""Regression: broker-posted expiration must close a short PPG call."""
from datetime import date, datetime
from decimal import Decimal

import pytest

from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor


PPG = OptionContract("PPG", date(2026, 9, 18), Decimal("105"), OptionType.CALL)


def _attribution(record_date, *, instrument=PPG, offset=Decimal("1")):
    book = LotBook()
    book.seed(Lot("ppg-opening", PPG, Decimal("-1"), datetime(2026, 8, 31),
                  None, campaign_id="andrew-ppg"))
    event = PositionEvent(
        kind=PositionEventKind.EXPIRATION,
        changes=(PositionChange(PPG, offset),),
        occurred_at=datetime(2026, 9, 21),
    )
    record = RealizedGainLossRecord(
        record_date, instrument, Decimal("1"), Decimal("0"),
        Decimal("100"), Decimal("0"), Decimal("100"), "FIFO", "SHORT TERM",
    )
    return RealizedLotAttributor(book).attribute([], [record], [event])


@pytest.mark.parametrize("realized_date", [date(2026, 9, 18), date(2026, 9, 21)])
def test_ppg_short_call_expiration_attributed_on_expiry_or_posting_date(realized_date):
    result = _attribution(realized_date)[0]
    assert result.allocated_quantity == Decimal("1")
    assert result.allocations[0].lot_id == "ppg-opening"
    assert result.allocations[0].campaign_id == "andrew-ppg"


def test_ppg_expiration_does_not_match_another_strike():
    other = OptionContract("PPG", date(2026, 9, 18), Decimal("110"), OptionType.CALL)
    with pytest.raises(ValueError, match="No CampaignIQ closing activity"):
        _attribution(date(2026, 9, 18), instrument=other)


def test_ppg_expiration_does_not_match_unrelated_date():
    with pytest.raises(ValueError, match="No CampaignIQ closing activity"):
        _attribution(date(2026, 9, 16))


def test_ppg_expiration_without_opening_lot_cannot_create_false_closure():
    book = LotBook()
    event = PositionEvent(PositionEventKind.EXPIRATION,
                          (PositionChange(PPG, Decimal("1")),), datetime(2026, 9, 21))
    record = RealizedGainLossRecord(date(2026, 9, 18), PPG, Decimal("1"),
                                    Decimal("0"), Decimal("100"), Decimal("0"),
                                    Decimal("100"), "FIFO", "SHORT TERM")
    with pytest.raises(ValueError, match="No CampaignIQ closing activity"):
        RealizedLotAttributor(book).attribute([], [record], [event])
