from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.lot_book_period_applier import LotBookPeriodApplier
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument


D = Decimal


def _opening_stock_trade() -> Trade:
    return Trade(
        legs=(
            InstrumentLeg(
                instrument=Instrument("IBM"),
                side=Side.BUY,
                position_effect=PositionEffect.OPEN,
                executions=(
                    Execution(
                        D("100"),
                        D("200"),
                        datetime(2026, 7, 2, 10, 0),
                    ),
                ),
            ),
        )
    )


def test_period_applier_preserves_trade_campaign_and_does_not_mutate_opening_book() -> None:
    opening = LotBook()
    history = PositionHistory()

    trade = _opening_stock_trade()
    history.add_trade(trade)

    campaign = Campaign(
        campaign_id="CAMP-000042",
        trades=(trade,),
    )

    ending = LotBookPeriodApplier().apply(
        opening_lot_book=opening,
        position_history=history,
        campaigns=(campaign,),
    )

    assert opening.lots(Instrument("IBM")) == ()

    lots = ending.lots(Instrument("IBM"))
    assert len(lots) == 1
    assert lots[0].quantity == D("100")
    assert lots[0].campaign_id == "CAMP-000042"


def test_period_applier_propagates_assignment_campaign_to_created_equity_lot() -> None:
    option = OptionContract(
        underlying="COIN",
        expiration=date(2026, 7, 17),
        strike=D("360"),
        option_type=OptionType.PUT,
    )

    opening = LotBook()
    opening.seed(
        Lot(
            lot_id="OPEN-COIN-PUT",
            instrument=option,
            quantity=D("-5"),
            opened_at=datetime(2026, 6, 30, 16, 0),
            basis_total=None,
            basis_source=None,
            campaign_id="CAMP-HISTORICAL-COIN",
        )
    )

    event = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        occurred_at=datetime(2026, 7, 17, 16, 0),
        changes=(
            PositionChange(
                instrument=option,
                quantity=D("5"),
            ),
            PositionChange(
                instrument=Instrument("COIN"),
                quantity=D("-500"),
            ),
        ),
    )

    history = PositionHistory()
    history.add_event(event)

    ending = LotBookPeriodApplier().apply(
        opening_lot_book=opening,
        position_history=history,
        campaigns=(),
    )

    assert opening.lots(option)[0].quantity == D("-5")
    assert ending.lots(option) == ()

    equity_lots = ending.lots(Instrument("COIN"))
    assert len(equity_lots) == 1
    assert equity_lots[0].quantity == D("-500")
    assert equity_lots[0].campaign_id == "CAMP-HISTORICAL-COIN"
