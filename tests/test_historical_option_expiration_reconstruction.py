from datetime import date, datetime
from decimal import Decimal
from campaigniq.domain.execution import Execution
from campaigniq.domain.historical_lot_reconstructor import HistoricalLotReconstructor
from campaigniq.domain.leg import Leg
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event import PositionChange, PositionEvent, PositionEventKind
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument

def short_call(symbol):
    contract=OptionContract(underlying=symbol, expiration=date(2026,7,17), strike=Decimal("162.5"), option_type=OptionType.CALL)
    trade=Trade(legs=(Leg(instrument=contract,side=Side.SELL,position_effect=PositionEffect.OPEN,executions=(Execution(quantity=Decimal("-1"),execution_price=Decimal("10"),executed_at=datetime(2026,7,2,10,0)),)),))
    return contract,trade

def equity_expiration(symbol, when=datetime(2026,7,18,0,8,15)):
    return PositionEvent(kind=PositionEventKind.EXPIRATION,changes=(PositionChange(instrument=Instrument(symbol),quantity=Decimal("-100")),),occurred_at=when)

def test_assigned_historical_call_is_not_reseeded():
    contract,trade=short_call("CVX")
    book=LotBook()
    HistoricalLotReconstructor.seed_missing_option_lots(book,(trade,),historical_expiration_events=[equity_expiration("CVX")])
    assert book.lots(contract) == ()

def test_unrelated_expiration_does_not_suppress_option():
    contract,trade=short_call("CVX")
    book=LotBook()
    HistoricalLotReconstructor.seed_missing_option_lots(book,(trade,),historical_expiration_events=[equity_expiration("DELL")])
    assert len(book.lots(contract)) == 1

def test_expiration_before_open_does_not_suppress_option():
    contract,trade=short_call("CVX")
    book=LotBook()
    HistoricalLotReconstructor.seed_missing_option_lots(book,(trade,),historical_expiration_events=[equity_expiration("CVX",datetime(2026,7,1))])
    assert len(book.lots(contract)) == 1
