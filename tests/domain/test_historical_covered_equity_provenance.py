from datetime import date, datetime
from decimal import Decimal

from campaigniq.campaign_reconstructor import CampaignReconstructor
from campaigniq.domain.campaign import Campaign
from campaigniq.domain.execution import Execution
from campaigniq.domain.historical_lot_reconstructor import (
    HistoricalLotReconstructor,
)
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument


def _execution(
    quantity: str,
    price: str,
    when: datetime,
) -> Execution:
    return Execution(
        quantity=Decimal(quantity),
        execution_price=Decimal(price),
        executed_at=when,
    )


def test_assigns_existing_covered_equity_provenance_through_call_roll() -> None:
    underlying = Instrument("AMT")

    july_call = OptionContract(
        underlying="AMT",
        expiration=date(2026, 7, 17),
        strike=Decimal("170"),
        option_type=OptionType.CALL,
    )

    august_call = OptionContract(
        underlying="AMT",
        expiration=date(2026, 8, 21),
        strike=Decimal("155"),
        option_type=OptionType.CALL,
    )

    # Historical evidence: the shares and July call were opened together
    # as one covered position in June.
    historical_trade = Trade(
        legs=(
            OptionLeg(
                contract=july_call,
                side=Side.SELL,
                position_effect=PositionEffect.OPEN,
                executions=(
                    _execution(
                        "-1",
                        "11.15",
                        datetime(2026, 6, 23, 10, 54, 22),
                    ),
                ),
                broker_strategy="COVERED",
            ),
            InstrumentLeg(
                instrument=underlying,
                side=Side.BUY,
                position_effect=PositionEffect.OPEN,
                executions=(
                    _execution(
                        "100",
                        "178.57",
                        datetime(2026, 6, 23, 10, 54, 22),
                    ),
                ),
            ),
        )
    )

    # Current-period campaign rolls the historically covered short call.
    july_campaign = Campaign(
        campaign_id="CAMP-JULY",
        trades=(
            Trade(
                legs=(
                    OptionLeg(
                        contract=august_call,
                        side=Side.SELL,
                        position_effect=PositionEffect.OPEN,
                        executions=(
                            _execution(
                                "-1",
                                "16.31",
                                datetime(2026, 7, 17, 11, 52, 55),
                            ),
                        ),
                        broker_strategy="DIAGONAL",
                    ),
                    OptionLeg(
                        contract=july_call,
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            _execution(
                                "1",
                                "0.21",
                                datetime(2026, 7, 17, 11, 52, 55),
                            ),
                        ),
                        broker_strategy="",
                    ),
                )
            ),
        ),
        started_before_data=True,
    )

    book = LotBook()

    book.seed(
        Lot(
            lot_id="SNAPSHOT:AMT",
            instrument=underlying,
            quantity=Decimal("100"),
            opened_at=datetime(2026, 6, 30),
            basis_total=Decimal("19015.03"),
            basis_source="SCHWAB_POSITION_SNAPSHOT",
        )
    )

    book.seed(
        Lot(
            lot_id="SNAPSHOT:AMT-JUL17-170C",
            instrument=july_call,
            quantity=Decimal("-1"),
            opened_at=datetime(2026, 6, 30),
            basis_total=Decimal("-1115"),
            basis_source="SCHWAB_POSITION_SNAPSHOT",
        )
    )

    reconstructor = HistoricalLotReconstructor(
        campaign_reconstructor=CampaignReconstructor(),
    )

    reconstructor.assign_rolled_covered_equity_provenance(
        book,
        (july_campaign,),
        (historical_trade,),
    )

    equity_lots = book.lots(underlying)

    assert len(equity_lots) == 1

    lot = equity_lots[0]

    # Provenance changes; broker lot economics do not.
    assert lot.lot_id == "SNAPSHOT:AMT"
    assert lot.quantity == Decimal("100")
    assert lot.basis_total == Decimal("19015.03")
    assert lot.basis_source == "SCHWAB_POSITION_SNAPSHOT"
    assert lot.campaign_id == "CAMP-JULY"


def test_does_not_assign_equity_when_history_proves_only_short_call() -> None:
    underlying = Instrument("AMT")

    july_call = OptionContract(
        underlying="AMT",
        expiration=date(2026, 7, 17),
        strike=Decimal("170"),
        option_type=OptionType.CALL,
    )

    august_call = OptionContract(
        underlying="AMT",
        expiration=date(2026, 8, 21),
        strike=Decimal("155"),
        option_type=OptionType.CALL,
    )

    # Historical evidence contains only the short call.  It does not prove
    # that any AMT shares belonged to the same covered position.
    historical_trade = Trade(
        legs=(
            OptionLeg(
                contract=july_call,
                side=Side.SELL,
                position_effect=PositionEffect.OPEN,
                executions=(
                    _execution(
                        "-1",
                        "11.15",
                        datetime(2026, 6, 23, 10, 54, 22),
                    ),
                ),
                broker_strategy="SINGLE",
            ),
        )
    )

    july_campaign = Campaign(
        campaign_id="CAMP-JULY",
        trades=(
            Trade(
                legs=(
                    OptionLeg(
                        contract=august_call,
                        side=Side.SELL,
                        position_effect=PositionEffect.OPEN,
                        executions=(
                            _execution(
                                "-1",
                                "16.31",
                                datetime(2026, 7, 17, 11, 52, 55),
                            ),
                        ),
                        broker_strategy="DIAGONAL",
                    ),
                    OptionLeg(
                        contract=july_call,
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            _execution(
                                "1",
                                "0.21",
                                datetime(2026, 7, 17, 11, 52, 55),
                            ),
                        ),
                        broker_strategy="",
                    ),
                )
            ),
        ),
        started_before_data=True,
    )

    book = LotBook()

    book.seed(
        Lot(
            lot_id="SNAPSHOT:AMT",
            instrument=underlying,
            quantity=Decimal("100"),
            opened_at=datetime(2026, 6, 30),
            basis_total=Decimal("19015.03"),
            basis_source="SCHWAB_POSITION_SNAPSHOT",
        )
    )

    book.seed(
        Lot(
            lot_id="SNAPSHOT:AMT-JUL17-170C",
            instrument=july_call,
            quantity=Decimal("-1"),
            opened_at=datetime(2026, 6, 30),
            basis_total=Decimal("-1115"),
            basis_source="SCHWAB_POSITION_SNAPSHOT",
        )
    )

    reconstructor = HistoricalLotReconstructor(
        campaign_reconstructor=CampaignReconstructor(),
    )

    reconstructor.assign_rolled_covered_equity_provenance(
        book,
        (july_campaign,),
        (historical_trade,),
    )

    equity_lot = book.lots(underlying)[0]

    assert equity_lot.campaign_id is None


def test_does_not_assign_ambiguous_equity_quantity() -> None:
    underlying = Instrument("AMT")

    july_call = OptionContract(
        underlying="AMT",
        expiration=date(2026, 7, 17),
        strike=Decimal("170"),
        option_type=OptionType.CALL,
    )

    august_call = OptionContract(
        underlying="AMT",
        expiration=date(2026, 8, 21),
        strike=Decimal("155"),
        option_type=OptionType.CALL,
    )

    historical_trade = Trade(
        legs=(
            OptionLeg(
                contract=july_call,
                side=Side.SELL,
                position_effect=PositionEffect.OPEN,
                executions=(
                    _execution(
                        "-1",
                        "11.15",
                        datetime(2026, 6, 23, 10, 54, 22),
                    ),
                ),
                broker_strategy="COVERED",
            ),
            InstrumentLeg(
                instrument=underlying,
                side=Side.BUY,
                position_effect=PositionEffect.OPEN,
                executions=(
                    _execution(
                        "100",
                        "178.57",
                        datetime(2026, 6, 23, 10, 54, 22),
                    ),
                ),
            ),
        )
    )

    july_campaign = Campaign(
        campaign_id="CAMP-JULY",
        trades=(
            Trade(
                legs=(
                    OptionLeg(
                        contract=august_call,
                        side=Side.SELL,
                        position_effect=PositionEffect.OPEN,
                        executions=(
                            _execution(
                                "-1",
                                "16.31",
                                datetime(2026, 7, 17, 11, 52, 55),
                            ),
                        ),
                        broker_strategy="DIAGONAL",
                    ),
                    OptionLeg(
                        contract=july_call,
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            _execution(
                                "1",
                                "0.21",
                                datetime(2026, 7, 17, 11, 52, 55),
                            ),
                        ),
                        broker_strategy="",
                    ),
                )
            ),
        ),
        started_before_data=True,
    )

    book = LotBook()

    book.seed(
        Lot(
            lot_id="SNAPSHOT:AMT-A",
            instrument=underlying,
            quantity=Decimal("100"),
            opened_at=datetime(2026, 6, 30),
            basis_total=Decimal("19015.03"),
            basis_source="SCHWAB_POSITION_SNAPSHOT",
        )
    )

    book.seed(
        Lot(
            lot_id="SNAPSHOT:AMT-B",
            instrument=underlying,
            quantity=Decimal("100"),
            opened_at=datetime(2026, 6, 30),
            basis_total=Decimal("18000"),
            basis_source="SCHWAB_POSITION_SNAPSHOT",
        )
    )

    book.seed(
        Lot(
            lot_id="SNAPSHOT:AMT-JUL17-170C",
            instrument=july_call,
            quantity=Decimal("-1"),
            opened_at=datetime(2026, 6, 30),
            basis_total=Decimal("-1115"),
            basis_source="SCHWAB_POSITION_SNAPSHOT",
        )
    )

    reconstructor = HistoricalLotReconstructor(
        campaign_reconstructor=CampaignReconstructor(),
    )

    reconstructor.assign_rolled_covered_equity_provenance(
        book,
        (july_campaign,),
        (historical_trade,),
    )

    equity_lots = book.lots(underlying)

    assert len(equity_lots) == 2
    assert all(lot.campaign_id is None for lot in equity_lots)


def test_does_not_seed_duplicate_covered_equity_when_carried_lot_has_provenance() -> None:
    underlying = Instrument("LMT")

    august_call = OptionContract(
        underlying="LMT",
        expiration=date(2026, 8, 21),
        strike=Decimal("480"),
        option_type=OptionType.CALL,
    )

    # July proves that the short call and 100 LMT shares were opened
    # together as a covered position.
    historical_trade = Trade(
        legs=(
            OptionLeg(
                contract=august_call,
                side=Side.SELL,
                position_effect=PositionEffect.OPEN,
                executions=(
                    _execution(
                        "-1",
                        "12.00",
                        datetime(2026, 7, 20, 10, 26, 3),
                    ),
                ),
                broker_strategy="COVERED",
            ),
            InstrumentLeg(
                instrument=underlying,
                side=Side.BUY,
                position_effect=PositionEffect.OPEN,
                executions=(
                    _execution(
                        "100",
                        "470.00",
                        datetime(2026, 7, 20, 10, 26, 3),
                    ),
                ),
            ),
        )
    )

    # August closes the historically opened covered position.
    august_campaign = Campaign(
        campaign_id="CAMP-AUGUST-LMT",
        trades=(
            Trade(
                legs=(
                    OptionLeg(
                        contract=august_call,
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            _execution(
                                "1",
                                "1.00",
                                datetime(2026, 8, 18, 10, 0),
                            ),
                        ),
                        broker_strategy="",
                    ),
                    InstrumentLeg(
                        instrument=underlying,
                        side=Side.SELL,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            _execution(
                                "-100",
                                "500.00",
                                datetime(2026, 8, 18, 10, 0),
                            ),
                        ),
                    ),
                )
            ),
        ),
        started_before_data=True,
    )

    book = LotBook()

    # This is a carried July lot.  Its campaign provenance is already
    # established, so it must still count as existing opening inventory.
    book.seed(
        Lot(
            lot_id="JULY-LMT-EQUITY",
            instrument=underlying,
            quantity=Decimal("100"),
            opened_at=datetime(2026, 7, 20, 10, 26, 3),
            basis_total=Decimal("47000"),
            basis_source="TRADE",
            campaign_id="CAMP-JULY-LMT",
        )
    )

    book.seed(
        Lot(
            lot_id="JULY-LMT-CALL",
            instrument=august_call,
            quantity=Decimal("-1"),
            opened_at=datetime(2026, 7, 20, 10, 26, 3),
            basis_total=Decimal("-1200"),
            basis_source="TRADE",
            campaign_id="CAMP-JULY-LMT",
        )
    )

    reconstructor = HistoricalLotReconstructor(
        campaign_reconstructor=CampaignReconstructor(),
    )

    reconstructor.seed_missing_covered_equity_lots(
        book,
        (august_campaign,),
        (historical_trade,),
    )

    equity_lots = book.lots(underlying)

    assert len(equity_lots) == 1
    assert equity_lots[0].lot_id == "JULY-LMT-EQUITY"
    assert equity_lots[0].quantity == Decimal("100")
    assert equity_lots[0].campaign_id == "CAMP-JULY-LMT"


def test_assignment_reconstruction_does_not_seed_duplicate_equity_when_carried_lot_has_provenance() -> None:
    underlying = Instrument("LMT")

    august_call = OptionContract(
        underlying="LMT",
        expiration=date(2026, 8, 21),
        strike=Decimal("480"),
        option_type=OptionType.CALL,
    )

    historical_trade = Trade(
        legs=(
            OptionLeg(
                contract=august_call,
                side=Side.SELL,
                position_effect=PositionEffect.OPEN,
                executions=(
                    _execution(
                        "-1",
                        "12.00",
                        datetime(2026, 7, 20, 10, 26, 3),
                    ),
                ),
                broker_strategy="COVERED",
            ),
            InstrumentLeg(
                instrument=underlying,
                side=Side.BUY,
                position_effect=PositionEffect.OPEN,
                executions=(
                    _execution(
                        "100",
                        "470.00",
                        datetime(2026, 7, 20, 10, 26, 3),
                    ),
                ),
            ),
        )
    )

    assignment = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=august_call,
                quantity=Decimal("1"),
            ),
            PositionChange(
                instrument=underlying,
                quantity=Decimal("-100"),
            ),
        ),
        occurred_at=datetime(2026, 8, 21),
    )

    book = LotBook()

    book.seed(
        Lot(
            lot_id="JULY-LMT-EQUITY",
            instrument=underlying,
            quantity=Decimal("100"),
            opened_at=datetime(2026, 7, 20, 10, 26, 3),
            basis_total=Decimal("47000"),
            basis_source="TRADE",
            campaign_id="CAMP-JULY-LMT",
        )
    )

    book.seed(
        Lot(
            lot_id="JULY-LMT-CALL",
            instrument=august_call,
            quantity=Decimal("-1"),
            opened_at=datetime(2026, 7, 20, 10, 26, 3),
            basis_total=Decimal("-1200"),
            basis_source="TRADE",
            campaign_id="CAMP-JULY-LMT",
        )
    )

    reconstructor = HistoricalLotReconstructor(
        campaign_reconstructor=CampaignReconstructor(),
    )

    reconstructor.seed_missing_assignment_covered_equity_lots(
        book,
        (assignment,),
        (historical_trade,),
    )

    equity_lots = book.lots(underlying)

    assert len(equity_lots) == 1
    assert equity_lots[0].lot_id == "JULY-LMT-EQUITY"
    assert equity_lots[0].quantity == Decimal("100")
    assert equity_lots[0].campaign_id == "HIST-CAMP-000001"


def test_rebinds_carried_covered_equity_provenance_through_call_roll() -> None:
    underlying = Instrument("IBM")

    july_call = OptionContract(
        underlying="IBM",
        expiration=date(2026, 7, 17),
        strike=Decimal("247.5"),
        option_type=OptionType.CALL,
    )

    august_call = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("195"),
        option_type=OptionType.CALL,
    )

    # Historical evidence proves that the shares and July call were opened
    # together as one covered position in the preceding period.
    historical_trade = Trade(
        legs=(
            OptionLeg(
                contract=july_call,
                side=Side.SELL,
                position_effect=PositionEffect.OPEN,
                executions=(
                    _execution(
                        "-1",
                        "23.25",
                        datetime(2026, 6, 23, 11, 29, 55),
                    ),
                ),
                broker_strategy="COVERED",
            ),
            InstrumentLeg(
                instrument=underlying,
                side=Side.BUY,
                position_effect=PositionEffect.OPEN,
                executions=(
                    _execution(
                        "100",
                        "265.95",
                        datetime(2026, 6, 23, 11, 29, 55),
                    ),
                ),
            ),
        )
    )

    # The current-period campaign rolls the historically covered short call.
    july_campaign = Campaign(
        campaign_id="CAMP-JULY",
        trades=(
            Trade(
                legs=(
                    OptionLeg(
                        contract=august_call,
                        side=Side.SELL,
                        position_effect=PositionEffect.OPEN,
                        executions=(
                            _execution(
                                "-1",
                                "21.87",
                                datetime(2026, 7, 17, 12, 30, 3),
                            ),
                        ),
                        broker_strategy="DIAGONAL",
                    ),
                    OptionLeg(
                        contract=july_call,
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            _execution(
                                "1",
                                "0.03",
                                datetime(2026, 7, 17, 12, 30, 3),
                            ),
                        ),
                        broker_strategy="",
                    ),
                )
            ),
        ),
        started_before_data=True,
    )

    book = LotBook()

    book.seed(
        Lot(
            lot_id="CARRIED:IBM",
            instrument=underlying,
            quantity=Decimal("100"),
            opened_at=datetime(2026, 6, 23, 11, 29, 55),
            basis_total=None,
            basis_source=None,
            campaign_id="CAMP-JUNE",
        )
    )

    book.seed(
        Lot(
            lot_id="CARRIED:IBM-JUL17-247.5C",
            instrument=july_call,
            quantity=Decimal("-1"),
            opened_at=datetime(2026, 6, 23, 11, 29, 55),
            basis_total=None,
            basis_source=None,
            campaign_id="CAMP-JUNE",
        )
    )

    reconstructor = HistoricalLotReconstructor(
        campaign_reconstructor=CampaignReconstructor(),
    )

    reconstructor.assign_rolled_covered_equity_provenance(
        book,
        (july_campaign,),
        (historical_trade,),
    )

    equity_lots = book.lots(underlying)

    assert len(equity_lots) == 1

    lot = equity_lots[0]

    assert lot.lot_id == "CARRIED:IBM"
    assert lot.quantity == Decimal("100")

    # Carried campaign IDs are ancestry evidence, not globally stable IDs.
    # Once the roll proves continuity, provenance must be rebound to the
    # current period-local campaign.
    assert lot.campaign_id == "CAMP-JULY"


def test_rebinds_carried_equity_when_historical_campaign_closed_and_reopened_covered_stock() -> None:
    underlying = Instrument("IBM")

    june_call = OptionContract(
        underlying="IBM",
        expiration=date(2026, 6, 18),
        strike=Decimal("210"),
        option_type=OptionType.CALL,
    )

    july_call = OptionContract(
        underlying="IBM",
        expiration=date(2026, 7, 17),
        strike=Decimal("247.5"),
        option_type=OptionType.CALL,
    )

    august_call = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("195"),
        option_type=OptionType.CALL,
    )

    # Earlier in June, the preceding covered IBM position was closed.
    historical_close = Trade(
        legs=(
            OptionLeg(
                contract=june_call,
                side=Side.BUY,
                position_effect=PositionEffect.CLOSE,
                executions=(
                    _execution(
                        "1",
                        "60.90",
                        datetime(2026, 6, 16, 10, 48, 23),
                    ),
                ),
                broker_strategy="COVERED",
            ),
            InstrumentLeg(
                instrument=underlying,
                side=Side.SELL,
                position_effect=PositionEffect.CLOSE,
                executions=(
                    _execution(
                        "-100",
                        "270.76",
                        datetime(2026, 6, 16, 10, 48, 23),
                    ),
                ),
            ),
        )
    )

    # A week later, the shares and July call were opened together as a new
    # covered IBM position.
    historical_open = Trade(
        legs=(
            OptionLeg(
                contract=july_call,
                side=Side.SELL,
                position_effect=PositionEffect.OPEN,
                executions=(
                    _execution(
                        "-1",
                        "23.25",
                        datetime(2026, 6, 23, 11, 29, 55),
                    ),
                ),
                broker_strategy="COVERED",
            ),
            InstrumentLeg(
                instrument=underlying,
                side=Side.BUY,
                position_effect=PositionEffect.OPEN,
                executions=(
                    _execution(
                        "100",
                        "265.95",
                        datetime(2026, 6, 23, 11, 29, 55),
                    ),
                ),
            ),
        )
    )

    # July rolls the specific July call that was opened with those shares.
    july_campaign = Campaign(
        campaign_id="CAMP-JULY",
        trades=(
            Trade(
                legs=(
                    OptionLeg(
                        contract=august_call,
                        side=Side.SELL,
                        position_effect=PositionEffect.OPEN,
                        executions=(
                            _execution(
                                "-1",
                                "21.87",
                                datetime(2026, 7, 17, 12, 30, 3),
                            ),
                        ),
                        broker_strategy="DIAGONAL",
                    ),
                    OptionLeg(
                        contract=july_call,
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            _execution(
                                "1",
                                "0.03",
                                datetime(2026, 7, 17, 12, 30, 3),
                            ),
                        ),
                        broker_strategy="",
                    ),
                )
            ),
        ),
        started_before_data=True,
    )

    book = LotBook()

    book.seed(
        Lot(
            lot_id="CARRIED:IBM",
            instrument=underlying,
            quantity=Decimal("100"),
            opened_at=datetime(2026, 6, 23, 11, 29, 55),
            basis_total=None,
            basis_source=None,
            campaign_id="CAMP-JUNE",
        )
    )

    book.seed(
        Lot(
            lot_id="CARRIED:IBM-JUL17-247.5C",
            instrument=july_call,
            quantity=Decimal("-1"),
            opened_at=datetime(2026, 6, 23, 11, 29, 55),
            basis_total=None,
            basis_source=None,
            campaign_id="CAMP-JUNE",
        )
    )

    reconstructor = HistoricalLotReconstructor(
        campaign_reconstructor=CampaignReconstructor(),
    )

    reconstructor.assign_rolled_covered_equity_provenance(
        book,
        (july_campaign,),
        (
            historical_close,
            historical_open,
        ),
    )

    equity_lot = book.lots(underlying)[0]

    assert equity_lot.campaign_id == "CAMP-JULY"


def test_assignment_rebinds_carried_covered_equity_to_historical_campaign() -> None:
    underlying = Instrument("IBM")

    august_call = OptionContract(
        underlying="IBM",
        expiration=date(2026, 8, 21),
        strike=Decimal("195"),
        option_type=OptionType.CALL,
    )

    # Historical evidence proves that the shares and short call were opened
    # together as one covered position in the preceding period.
    historical_trade = Trade(
        legs=(
            OptionLeg(
                contract=august_call,
                side=Side.SELL,
                position_effect=PositionEffect.OPEN,
                executions=(
                    _execution(
                        "-1",
                        "21.87",
                        datetime(2026, 7, 17, 12, 30, 3),
                    ),
                ),
                broker_strategy="COVERED",
            ),
            InstrumentLeg(
                instrument=underlying,
                side=Side.BUY,
                position_effect=PositionEffect.OPEN,
                executions=(
                    _execution(
                        "100",
                        "265.95",
                        datetime(2026, 7, 17, 12, 30, 3),
                    ),
                ),
            ),
        )
    )

    assignment = PositionEvent(
        kind=PositionEventKind.ASSIGNMENT,
        changes=(
            PositionChange(
                instrument=august_call,
                quantity=Decimal("1"),
            ),
            PositionChange(
                instrument=underlying,
                quantity=Decimal("-100"),
            ),
        ),
        occurred_at=datetime(2026, 8, 7),
    )

    book = LotBook()

    # These IDs are valid only in the preceding period.  They are ancestry
    # evidence, not globally stable campaign identities.
    book.seed(
        Lot(
            lot_id="CARRIED:IBM",
            instrument=underlying,
            quantity=Decimal("100"),
            opened_at=datetime(2026, 7, 17, 12, 30, 3),
            basis_total=Decimal("26595"),
            basis_source="TRADE",
            campaign_id="CAMP-000012",
        )
    )

    book.seed(
        Lot(
            lot_id="CARRIED:IBM-AUG21-195C",
            instrument=august_call,
            quantity=Decimal("-1"),
            opened_at=datetime(2026, 7, 17, 12, 30, 3),
            basis_total=Decimal("-2187"),
            basis_source="TRADE",
            campaign_id="CAMP-000012",
        )
    )

    reconstructor = HistoricalLotReconstructor(
        campaign_reconstructor=CampaignReconstructor(),
    )

    reconstructor.seed_missing_assignment_covered_equity_lots(
        book,
        (assignment,),
        (historical_trade,),
    )

    equity_lots = book.lots(underlying)

    assert len(equity_lots) == 1
    assert equity_lots[0].lot_id == "CARRIED:IBM"
    assert equity_lots[0].quantity == Decimal("100")

    # The prior-period CAMP-* value must not survive into the new period,
    # where the same local ID may belong to an unrelated campaign.
    assert equity_lots[0].campaign_id == "HIST-CAMP-000001"
