from datetime import datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.historical_campaign_provenance import (
    HistoricalCampaignProvenanceResolver,
)
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade


def _option(
    *,
    underlying: str = "GS",
    expiration=(2026, 6, 18),
    strike: str = "720",
) -> OptionContract:
    from datetime import date

    return OptionContract(
        underlying=underlying,
        expiration=date(*expiration),
        strike=Decimal(strike),
        option_type=OptionType.CALL,
    )


def _trade(
    instrument: OptionContract,
    *,
    quantity: str,
    effect: PositionEffect,
) -> Trade:
    quantity_decimal = Decimal(quantity)

    return Trade(
        legs=(
            OptionLeg(
                contract=instrument,
                side=(
                    Side.SELL
                    if quantity_decimal < 0
                    else Side.BUY
                ),
                position_effect=effect,
                broker_strategy="",
                executions=(
                    Execution(
                        quantity=quantity_decimal,
                        execution_price=Decimal("1"),
                        executed_at=datetime(2026, 4, 21, 10, 0),
                    ),
                ),
            ),
        )
    )


def test_resolves_historical_campaign_to_surviving_opening_option_lot() -> None:
    instrument = _option()

    historical_trades = [
        _trade(
            instrument,
            quantity="-1",
            effect=PositionEffect.OPEN,
        ),
    ]

    book = LotBook()
    book.seed(
        Lot(
            lot_id="GS-LOT",
            instrument=instrument,
            quantity=Decimal("-1"),
            opened_at=datetime(2026, 4, 21, 10, 0),
            basis_total=Decimal("-21622"),
            basis_source="SCHWAB_POSITION_SNAPSHOT",
        )
    )

    HistoricalCampaignProvenanceResolver().resolve(
        historical_trades,
        book,
    )

    lots = book.lots(instrument)

    assert len(lots) == 1
    assert lots[0].campaign_id == "HIST-CAMP-000001"


def test_does_not_assign_when_historical_quantity_does_not_match() -> None:
    instrument = _option()

    historical_trades = [
        _trade(
            instrument,
            quantity="-1",
            effect=PositionEffect.OPEN,
        ),
    ]

    book = LotBook()
    book.seed(
        Lot(
            lot_id="GS-LOT",
            instrument=instrument,
            quantity=Decimal("-2"),
            opened_at=datetime(2026, 4, 21, 10, 0),
            basis_total=Decimal("-43244"),
            basis_source="SCHWAB_POSITION_SNAPSHOT",
        )
    )

    HistoricalCampaignProvenanceResolver().resolve(
        historical_trades,
        book,
    )

    assert book.lots(instrument)[0].campaign_id is None


def test_does_not_assign_when_multiple_historical_campaigns_claim_same_lot() -> None:
    instrument = _option()

    first = _trade(
        instrument,
        quantity="-1",
        effect=PositionEffect.OPEN,
    )

    second = _trade(
        instrument,
        quantity="-1",
        effect=PositionEffect.OPEN,
    )

    book = LotBook()
    book.seed(
        Lot(
            lot_id="GS-LOT",
            instrument=instrument,
            quantity=Decimal("-1"),
            opened_at=datetime(2026, 4, 21, 10, 0),
            basis_total=Decimal("-21622"),
            basis_source="SCHWAB_POSITION_SNAPSHOT",
        )
    )

    HistoricalCampaignProvenanceResolver().resolve(
        [first, second],
        book,
    )

    assert book.lots(instrument)[0].campaign_id is None
