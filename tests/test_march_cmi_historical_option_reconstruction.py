from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.historical_lot_reconstructor import (
    HistoricalLotReconstructor,
)


def test_missing_historical_option_lot_is_reconstructed_for_cmi() -> None:
    instrument = OptionContract(
        underlying="CMI",
        expiration=date(2026, 3, 20),
        strike=Decimal("540"),
        option_type=OptionType.CALL,
    )
    trade = Trade(
        legs=(
            Leg(
                instrument=instrument,
                side=Side.SELL,
                position_effect=PositionEffect.OPEN,
                executions=(
                    Execution(
                        quantity=Decimal("-1"),
                        execution_price=Decimal("44.56"),
                        executed_at=datetime(2026, 2, 27, 8, 26, 19),
                    ),
                ),
            ),
        )
    )

    book = LotBook()
    HistoricalLotReconstructor.seed_missing_option_lots(book, (trade,))

    lots = book.lots(instrument)
    assert len(lots) == 1
    assert lots[0].quantity == Decimal("-1")
    assert lots[0].opened_at == datetime(2026, 2, 27, 8, 26, 19)
    assert lots[0].basis_total is None
    assert lots[0].basis_source == "HISTORICAL_TRADE_RECONSTRUCTION"
