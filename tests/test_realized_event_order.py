"""Assignment-created lots and trades must share chronological lot identities."""
from datetime import date, datetime
from decimal import Decimal as D
import pytest
from campaigniq.domain.campaign import Campaign
from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect as Effect
from campaigniq.domain.position_event import PositionEvent, PositionChange
from campaigniq.domain.position_event_kind import PositionEventKind as Kind
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.historical_backfill_realized_replay import ChronologicalRealizedLotAttributor


@pytest.mark.parametrize('reverse_trades',[False,True])
@pytest.mark.parametrize('reverse_events',[False,True])
def test_spot_assignment_lots_precede_option_opening_without_changing_money(reverse_trades,reverse_events):
    stock=Instrument('SPOT')
    put=OptionContract('SPOT',date(2026,2,20),D(610),OptionType.PUT)
    call=OptionContract('SPOT',date(2026,3,20),D(480),OptionType.CALL)
    book=LotBook();book.seed(Lot('OPENING-SPOT-PUT',put,D(-5),datetime(2025,12,24),D('-25071.69')))
    events=[PositionEvent(Kind.ASSIGNMENT,(PositionChange(put,D(2)),PositionChange(stock,D(200))),datetime(2026,2,5)),
        PositionEvent(Kind.ASSIGNMENT,(PositionChange(put,D(3)),PositionChange(stock,D(300))),datetime(2026,2,6))]
    trades=[Trade((Leg(call,Side.SELL,Effect.OPEN,(Execution(D(5),D('11.05'),datetime(2026,2,6,10,47,6)),)),)),
        Trade((Leg(call,Side.BUY,Effect.CLOSE,(Execution(D(5),D('29.20'),datetime(2026,2,20,8,36,58)),)),))]
    if reverse_events:events.reverse()
    if reverse_trades:trades.reverse()
    record=RealizedGainLossRecord(date(2026,2,23),call,D(5),D('11.04'),D('5521.68'),D('14603.30'),D('-9081.62'),'FIFO','SHORT')
    expected=ChronologicalRealizedLotAttributor(book.clone()).attribute_campaigns([Campaign('SPOT-CAMPAIGN',tuple(trades))],[record],events)
    engine=RealizedLotAttributor(book.clone())
    actual=engine.attribute_campaigns([Campaign('SPOT-CAMPAIGN',tuple(trades))],[record],events)
    assert actual==expected
    assert actual[0].record is record
    assert [(a.lot_id,a.quantity,a.broker_basis,a.campaign_id) for a in actual[0].allocations]==[
        ('LOT-000003',D(5),D('14603.30'),'SPOT-CAMPAIGN')]
    assert [(lot.lot_id,lot.quantity) for lot in engine.lot_book.lots(stock)]==[
        ('LOT-000001',D(200)),('LOT-000002',D(300))]


def test_future_assignment_settlement_is_reserved_without_double_delivery():
    # The assignment event precedes the broker's partial delivery execution.
    stock=Instrument('DXCM');call=OptionContract('DXCM',date(2026,1,9),D(67),OptionType.CALL)
    book=LotBook();book.seed(Lot('ORIGINAL-STOCK',stock,D(500),datetime(2025,12,31),D(34835)))
    event=PositionEvent(Kind.ASSIGNMENT,(PositionChange(call,D(5)),PositionChange(stock,D(-500))),datetime(2026,1,9,16))
    trade=Trade((Leg(stock,Side.SELL,Effect.CLOSE,(Execution(D(300),D(67),datetime(2026,1,12,10)),)),))
    engine=RealizedLotAttributor(book)
    assert engine.attribute([trade],[],[event])==()
    assert book.lots(stock)==()
