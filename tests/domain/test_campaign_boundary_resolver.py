from datetime import datetime
from decimal import Decimal

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.execution import Execution
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.leg import Leg
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.value_objects.instrument import Instrument


def _close_trade(
    symbol: str,
    quantity: str,
    when: datetime,
) -> Trade:
    return Trade(
        legs=(
            InstrumentLeg(
                instrument=Instrument(symbol),
                side=Side.SELL,
                position_effect=PositionEffect.CLOSE,
                executions=(
                    Execution(
                        quantity=Decimal(quantity),
                        execution_price=Decimal("100"),
                        executed_at=when,
                    ),
                ),
            ),
        ),
    )


def test_boundary_campaign_can_claim_exact_preperiod_lot() -> None:
    historical_lot = Lot(
        lot_id="DEC-IBM",
        instrument=Instrument("IBM"),
        quantity=Decimal("100"),
        opened_at=datetime(2025, 12, 31, 16, 0),
        basis_total=Decimal("9000"),
        basis_source="DECEMBER_SNAPSHOT",
    )

    campaign = Campaign(
        campaign_id="CAMP-000001",
        trades=(
            _close_trade(
                "IBM",
                "100",
                datetime(2026, 1, 10, 10, 0),
            ),
        ),
        started_before_data=True,
    )

    book = LotBook()
    book.seed(historical_lot)

    # This is intentionally the interface we want to establish.
    assignments = book.resolve_boundary_campaign(campaign)

    assert assignments == {
        "DEC-IBM": "CAMP-000001",
    }

    assert book.lots(Instrument("IBM"))[0].campaign_id == "CAMP-000001"


def test_boundary_campaign_may_resolve_after_multiple_partial_closes() -> None:
    book = LotBook()

    book.seed(
        Lot(
            lot_id="DEC-LMT",
            instrument=Instrument("LMT-480P"),
            quantity=Decimal("-5"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("20"),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-000002",
        trades=(
            Trade(
                legs=(
                    Leg(
                        instrument=Instrument("LMT-480P"),
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            Execution(
                                quantity=Decimal("1"),
                                execution_price=Decimal("30"),
                                executed_at=datetime(2026, 1, 5, 10, 0),
                            ),
                        ),
                    ),
                )
            ),
            Trade(
                legs=(
                    Leg(
                        instrument=Instrument("LMT-480P"),
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            Execution(
                                quantity=Decimal("4"),
                                execution_price=Decimal("30"),
                                executed_at=datetime(2026, 1, 6, 10, 0),
                            ),
                        ),
                    ),
                )
            ),
        ),
        started_before_data=True,
    )

    assignments = book.resolve_boundary_campaign(campaign)

    assert assignments == {
        "DEC-LMT": "CAMP-000002",
    }

    assert book.lots(Instrument("LMT-480P"))[0].campaign_id == "CAMP-000002"


def test_boundary_campaign_does_not_claim_partially_consumed_lot() -> None:
    book = LotBook()

    book.seed(
        Lot(
            lot_id="DEC-IBM",
            instrument=Instrument("IBM"),
            quantity=Decimal("100"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("9000"),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-000003",
        trades=(
            _close_trade(
                "IBM",
                "40",
                datetime(2026, 1, 10, 10, 0),
            ),
        ),
        started_before_data=True,
    )

    assignments = book.resolve_boundary_campaign(campaign)

    assert assignments == {}
    assert book.lots(Instrument("IBM"))[0].campaign_id is None
    assert book.lots(Instrument("IBM"))[0].quantity == Decimal("100")


def test_non_boundary_campaign_does_not_claim_historical_lot() -> None:
    book = LotBook()

    book.seed(
        Lot(
            lot_id="DEC-IBM",
            instrument=Instrument("IBM"),
            quantity=Decimal("100"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("9000"),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-000004",
        trades=(
            Trade(
                legs=(
                    Leg(
                        instrument=Instrument("IBM"),
                        side=Side.BUY,
                        position_effect=PositionEffect.OPEN,
                        executions=(
                            Execution(
                                quantity=Decimal("100"),
                                execution_price=Decimal("95"),
                                executed_at=datetime(2026, 1, 10, 10, 0),
                            ),
                        ),
                    ),
                )
            ),
        ),
        started_before_data=False,
    )

    assignments = book.resolve_boundary_campaign(campaign)

    assert assignments == {}
    assert book.lots(Instrument("IBM"))[0].campaign_id is None

def test_mixed_open_and_close_campaign_can_claim_historical_close_lot() -> None:
    historical_lot = Lot(
        lot_id="DEC-COIN-220C",
        instrument=Instrument("COIN-220C"),
        quantity=Decimal("-5"),
        opened_at=datetime(2025, 12, 31, 16, 0),
        basis_total=Decimal("20000"),
        basis_source="DECEMBER_SNAPSHOT",
    )

    # The real-world pattern we're protecting:
    # one trade opens a new position while closing an older,
    # pre-period position in the same campaign.
    mixed_trade = Trade(
        legs=(
            Leg(
                instrument=Instrument("COIN-220C"),
                side=Side.BUY,
                position_effect=PositionEffect.CLOSE,
                executions=(
                    Execution(
                        quantity=Decimal("5"),
                        execution_price=Decimal("20"),
                        executed_at=datetime(2026, 1, 5, 10, 0),
                    ),
                ),
            ),
            Leg(
                instrument=Instrument("COIN-200C"),
                side=Side.SELL,
                position_effect=PositionEffect.OPEN,
                executions=(
                    Execution(
                        quantity=Decimal("5"),
                        execution_price=Decimal("30"),
                        executed_at=datetime(2026, 1, 5, 10, 0),
                    ),
                ),
            ),
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-000005",
        trades=(mixed_trade,),
        started_before_data=False,
    )

    book = LotBook()
    book.seed(historical_lot)

    assignments = book.resolve_boundary_campaign(campaign)

    assert assignments == {
        "DEC-COIN-220C": "CAMP-000005",
    }

    assert book.lots(Instrument("COIN-220C"))[0].campaign_id == "CAMP-000005"

def test_mixed_campaign_can_claim_historical_lots_consumed_by_later_trade() -> None:
    book = LotBook()

    book.seed(
        Lot(
            lot_id="DEC-COIN-220C",
            instrument=Instrument("COIN-220C"),
            quantity=Decimal("-5"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("20000"),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )

    book.seed(
        Lot(
            lot_id="DEC-COIN",
            instrument=Instrument("COIN"),
            quantity=Decimal("500"),
            opened_at=datetime(2025, 12, 31, 16, 0),
            basis_total=Decimal("125000"),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-000006",
        trades=(
            Trade(
                legs=(
                    Leg(
                        instrument=Instrument("COIN-220C"),
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            Execution(
                                quantity=Decimal("5"),
                                execution_price=Decimal("20"),
                                executed_at=datetime(2026, 1, 5, 10, 0),
                            ),
                        ),
                    ),
                    Leg(
                        instrument=Instrument("COIN-200C"),
                        side=Side.SELL,
                        position_effect=PositionEffect.OPEN,
                        executions=(
                            Execution(
                                quantity=Decimal("5"),
                                execution_price=Decimal("30"),
                                executed_at=datetime(2026, 1, 5, 10, 0),
                            ),
                        ),
                    ),
                ),
            ),
            Trade(
                legs=(
                    Leg(
                        instrument=Instrument("COIN-200C"),
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            Execution(
                                quantity=Decimal("5"),
                                execution_price=Decimal("24"),
                                executed_at=datetime(2026, 1, 23, 13, 0),
                            ),
                        ),
                    ),
                    Leg(
                        instrument=Instrument("COIN"),
                        side=Side.SELL,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            Execution(
                                quantity=Decimal("500"),
                                execution_price=Decimal("217"),
                                executed_at=datetime(2026, 1, 23, 13, 0),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        started_before_data=False,
    )

    assignments = book.resolve_boundary_campaign(campaign)

    assert assignments == {
        "DEC-COIN-220C": "CAMP-000006",
        "DEC-COIN": "CAMP-000006",
    }

    assert (
        book.lots(Instrument("COIN-220C"))[0].campaign_id
        == "CAMP-000006"
    )
    assert (
        book.lots(Instrument("COIN"))[0].campaign_id
        == "CAMP-000006"
    )
def test_same_instrument_multiple_historical_lots_can_be_claimed() -> None:
    book = LotBook()

    instrument = Instrument("CSCO-2026-05-15-72.5-C")

    book.seed(
        Lot(
            lot_id="HIST-1",
            instrument=instrument,
            quantity=Decimal("-2"),
            opened_at=datetime(2026, 3, 27, 10, 55, 41),
            basis_total=None,
            basis_source="HISTORICAL_TRADE_RECONSTRUCTION",
        )
    )

    book.seed(
        Lot(
            lot_id="HIST-2",
            instrument=instrument,
            quantity=Decimal("-3"),
            opened_at=datetime(2026, 3, 27, 10, 55, 41),
            basis_total=None,
            basis_source="HISTORICAL_TRADE_RECONSTRUCTION",
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-TEST",
        trades=(
            Trade(
                legs=(
                    Leg(
                        instrument=instrument,
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            Execution(
                                quantity=Decimal("5"),
                                execution_price=Decimal("10"),
                                executed_at=datetime(
                                    2026, 4, 14, 8, 59, 19
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        started_before_data=True,
    )

    assignments = book.resolve_boundary_campaign(campaign)

    assert assignments == {
        "HIST-1": "CAMP-TEST",
        "HIST-2": "CAMP-TEST",
    }

    assert all(
        lot.campaign_id == "CAMP-TEST"
        for lot in book.lots(instrument)
    )


def test_campaign_boundary_resolver_can_split_larger_opening_lot_when_partial_allowed() -> None:
    from campaigniq.domain.campaign_boundary_resolver import CampaignBoundaryResolver

    instrument = Instrument("MSFT")

    book = LotBook()
    book.seed(
        Lot(
            lot_id="APR-MSFT",
            instrument=instrument,
            quantity=Decimal("500"),
            opened_at=datetime(2026, 4, 30, 16, 0),
            basis_total=Decimal("231338.31"),
            basis_source="SCHWAB_POSITION_SNAPSHOT",
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-MAY-MSFT",
        trades=(
            _close_trade(
                "MSFT",
                "400",
                datetime(2026, 5, 29, 10, 0),
            ),
        ),
        started_before_data=True,
    )

    resolver = CampaignBoundaryResolver(book)

    assignments = resolver.resolve(
        campaign,
        allow_partial=True,
    )

    lots = book.lots(instrument)

    assert assignments
    assert len(lots) == 2

    assigned = [
        lot for lot in lots
        if lot.campaign_id == "CAMP-MAY-MSFT"
    ]
    unassigned = [
        lot for lot in lots
        if lot.campaign_id is None
    ]

    assert len(assigned) == 1
    assert len(unassigned) == 1

    assert assigned[0].quantity == Decimal("400")
    assert unassigned[0].quantity == Decimal("100")

    assert sum(
        (lot.quantity for lot in lots),
        Decimal("0"),
    ) == Decimal("500")

    assert sum(
        (
            lot.basis_total
            for lot in lots
            if lot.basis_total is not None
        ),
        Decimal("0"),
    ) == Decimal("231338.31")

    assert assigned[0].basis_source == "SCHWAB_POSITION_SNAPSHOT"
    assert unassigned[0].basis_source == "SCHWAB_POSITION_SNAPSHOT"

def test_campaign_boundary_resolver_can_split_larger_short_opening_lot_when_partial_allowed() -> None:
    from campaigniq.domain.campaign_boundary_resolver import CampaignBoundaryResolver

    instrument = Instrument("MSFT-340C")

    book = LotBook()
    book.seed(
        Lot(
            lot_id="APR-MSFT-CALL",
            instrument=instrument,
            quantity=Decimal("-5"),
            opened_at=datetime(2026, 4, 30, 16, 0),
            basis_total=Decimal("-43355.78"),
            basis_source="SCHWAB_POSITION_SNAPSHOT",
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-MAY-MSFT-CALL",
        trades=(
            Trade(
                legs=(
                    Leg(
                        instrument=instrument,
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            Execution(
                                quantity=Decimal("4"),
                                execution_price=Decimal("10"),
                                executed_at=datetime(2026, 5, 29, 10, 0),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        started_before_data=True,
    )

    resolver = CampaignBoundaryResolver(book)

    assignments = resolver.resolve(
        campaign,
        allow_partial=True,
    )

    lots = book.lots(instrument)

    assert assignments
    assert len(lots) == 2

    assigned = [
        lot for lot in lots
        if lot.campaign_id == "CAMP-MAY-MSFT-CALL"
    ]
    unassigned = [
        lot for lot in lots
        if lot.campaign_id is None
    ]

    assert len(assigned) == 1
    assert len(unassigned) == 1

    assert assigned[0].quantity == Decimal("-4")
    assert unassigned[0].quantity == Decimal("-1")

    assert sum(
        (lot.quantity for lot in lots),
        Decimal("0"),
    ) == Decimal("-5")

    assert sum(
        (
            lot.basis_total
            for lot in lots
            if lot.basis_total is not None
        ),
        Decimal("0"),
    ) == Decimal("-43355.78")

    assert assigned[0].basis_source == "SCHWAB_POSITION_SNAPSHOT"
    assert unassigned[0].basis_source == "SCHWAB_POSITION_SNAPSHOT"


def test_campaign_boundary_resolver_accepts_already_proven_carried_lot() -> None:
    """A carried lot with provenance still proves the opening position exists."""
    from campaigniq.domain.campaign_boundary_resolver import CampaignBoundaryResolver

    instrument = Instrument("LMT")

    book = LotBook()
    book.seed(
        Lot(
            lot_id="JULY-LMT",
            instrument=instrument,
            quantity=Decimal("100"),
            opened_at=datetime(2026, 7, 20, 10, 26, 3),
            basis_total=None,
            basis_source=None,
            campaign_id="CAMP-JULY-LMT",
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-AUGUST-LMT",
        trades=(
            _close_trade(
                "LMT",
                "100",
                datetime(2026, 8, 18, 10, 0),
            ),
        ),
        started_before_data=True,
    )

    resolver = CampaignBoundaryResolver(book)

    assignments = resolver.resolve(campaign)

    assert assignments == {
        "JULY-LMT": "CAMP-AUGUST-LMT",
    }

    # Once ancestry is proven at the new period boundary, the carried
    # lot must use the current period's campaign ID.  Period-local IDs
    # from the preceding reconstruction cannot safely survive here.
    resolved_lot = book.lots(instrument)[0]

    assert resolved_lot.campaign_id == "CAMP-AUGUST-LMT"
    assert resolved_lot.lot_id == "JULY-LMT"
    assert resolved_lot.instrument == instrument
    assert resolved_lot.quantity == Decimal("100")
    assert resolved_lot.opened_at == datetime(2026, 7, 20, 10, 26, 3)
    assert resolved_lot.basis_total is None
    assert resolved_lot.basis_source is None


def test_campaign_boundary_resolver_can_split_and_rebind_assigned_carried_long_lot() -> None:
    from campaigniq.domain.campaign_boundary_resolver import CampaignBoundaryResolver

    instrument = Instrument("MSFT")

    book = LotBook()
    book.seed(
        Lot(
            lot_id="APR-MSFT",
            instrument=instrument,
            quantity=Decimal("500"),
            opened_at=datetime(2026, 2, 20),
            basis_total=Decimal("231338.31"),
            basis_source="SCHWAB_POSITION_SNAPSHOT",
            campaign_id="CAMP-APR-MSFT",
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-MAY-MSFT",
        trades=(
            _close_trade(
                "MSFT",
                "400",
                datetime(2026, 5, 7, 8, 31, 24),
            ),
        ),
        started_before_data=True,
    )

    resolver = CampaignBoundaryResolver(book)

    assignments = resolver.resolve(
        campaign,
        allow_partial=True,
    )

    lots = book.lots(instrument)

    assert assignments == {
        "APR-MSFT": "CAMP-MAY-MSFT",
    }
    assert len(lots) == 2

    rebound = [
        lot for lot in lots
        if lot.campaign_id == "CAMP-MAY-MSFT"
    ]
    remainder = [
        lot for lot in lots
        if lot.campaign_id == "CAMP-APR-MSFT"
    ]

    assert len(rebound) == 1
    assert len(remainder) == 1

    assert rebound[0].lot_id == "APR-MSFT"
    assert rebound[0].quantity == Decimal("400")
    assert rebound[0].basis_total == Decimal("185070.648")

    assert remainder[0].lot_id != "APR-MSFT"
    assert remainder[0].quantity == Decimal("100")
    assert remainder[0].basis_total == Decimal("46267.662")

    assert rebound[0].opened_at == datetime(2026, 2, 20)
    assert remainder[0].opened_at == datetime(2026, 2, 20)

    assert rebound[0].basis_source == "SCHWAB_POSITION_SNAPSHOT"
    assert remainder[0].basis_source == "SCHWAB_POSITION_SNAPSHOT"

    assert sum(
        (lot.quantity for lot in lots),
        Decimal("0"),
    ) == Decimal("500")

    assert sum(
        (
            lot.basis_total
            for lot in lots
            if lot.basis_total is not None
        ),
        Decimal("0"),
    ) == Decimal("231338.31")


def test_campaign_boundary_resolver_can_split_and_rebind_assigned_carried_short_lot() -> None:
    from campaigniq.domain.campaign_boundary_resolver import CampaignBoundaryResolver

    instrument = Instrument("MSFT-340C")

    book = LotBook()
    book.seed(
        Lot(
            lot_id="APR-MSFT-CALL",
            instrument=instrument,
            quantity=Decimal("-5"),
            opened_at=datetime(2026, 4, 27, 8, 29, 50),
            basis_total=Decimal("-43355.78"),
            basis_source="SCHWAB_POSITION_SNAPSHOT",
            campaign_id="CAMP-APR-MSFT-CALL",
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-MAY-MSFT-CALL",
        trades=(
            Trade(
                legs=(
                    Leg(
                        instrument=instrument,
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            Execution(
                                quantity=Decimal("4"),
                                execution_price=Decimal("10"),
                                executed_at=datetime(2026, 5, 7, 8, 31, 24),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        started_before_data=True,
    )

    resolver = CampaignBoundaryResolver(book)

    assignments = resolver.resolve(
        campaign,
        allow_partial=True,
    )

    lots = book.lots(instrument)

    assert assignments == {
        "APR-MSFT-CALL": "CAMP-MAY-MSFT-CALL",
    }
    assert len(lots) == 2

    rebound = [
        lot for lot in lots
        if lot.campaign_id == "CAMP-MAY-MSFT-CALL"
    ]
    remainder = [
        lot for lot in lots
        if lot.campaign_id == "CAMP-APR-MSFT-CALL"
    ]

    assert len(rebound) == 1
    assert len(remainder) == 1

    assert rebound[0].lot_id == "APR-MSFT-CALL"
    assert rebound[0].quantity == Decimal("-4")
    assert rebound[0].basis_total == Decimal("-34684.624")

    assert remainder[0].lot_id != "APR-MSFT-CALL"
    assert remainder[0].quantity == Decimal("-1")
    assert remainder[0].basis_total == Decimal("-8671.156")

    assert rebound[0].opened_at == datetime(2026, 4, 27, 8, 29, 50)
    assert remainder[0].opened_at == datetime(2026, 4, 27, 8, 29, 50)

    assert rebound[0].basis_source == "SCHWAB_POSITION_SNAPSHOT"
    assert remainder[0].basis_source == "SCHWAB_POSITION_SNAPSHOT"

    assert sum(
        (lot.quantity for lot in lots),
        Decimal("0"),
    ) == Decimal("-5")

    assert sum(
        (
            lot.basis_total
            for lot in lots
            if lot.basis_total is not None
        ),
        Decimal("0"),
    ) == Decimal("-43355.78")


def test_campaign_boundary_resolver_can_select_unique_subset_of_assigned_carried_lots() -> None:
    from campaigniq.domain.campaign_boundary_resolver import CampaignBoundaryResolver

    instrument = Instrument("MSFT-340C")

    book = LotBook()

    for lot_id, quantity in (
        ("APR-MSFT-CALL-1", "-2"),
        ("APR-MSFT-CALL-2", "-2"),
        ("APR-MSFT-CALL-3", "-1"),
    ):
        book.seed(
            Lot(
                lot_id=lot_id,
                instrument=instrument,
                quantity=Decimal(quantity),
                opened_at=datetime(2026, 4, 27, 8, 29, 50),
                basis_total=None,
                basis_source=None,
                campaign_id="CAMP-APR-MSFT-CALL",
            )
        )

    campaign = Campaign(
        campaign_id="CAMP-MAY-MSFT-CALL",
        trades=(
            Trade(
                legs=(
                    Leg(
                        instrument=instrument,
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            Execution(
                                quantity=Decimal("4"),
                                execution_price=Decimal("10"),
                                executed_at=datetime(2026, 5, 7, 8, 31, 24),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        started_before_data=True,
    )

    resolver = CampaignBoundaryResolver(book)

    assignments = resolver.resolve(
        campaign,
        allow_partial=True,
    )

    lots = book.lots(instrument)

    assert assignments == {
        "APR-MSFT-CALL-1": "CAMP-MAY-MSFT-CALL",
        "APR-MSFT-CALL-2": "CAMP-MAY-MSFT-CALL",
    }

    rebound = [
        lot
        for lot in lots
        if lot.campaign_id == "CAMP-MAY-MSFT-CALL"
    ]
    remainder = [
        lot
        for lot in lots
        if lot.campaign_id == "CAMP-APR-MSFT-CALL"
    ]

    assert {lot.lot_id for lot in rebound} == {
        "APR-MSFT-CALL-1",
        "APR-MSFT-CALL-2",
    }
    assert sum(
        (lot.quantity for lot in rebound),
        Decimal("0"),
    ) == Decimal("-4")

    assert len(remainder) == 1
    assert remainder[0].lot_id == "APR-MSFT-CALL-3"
    assert remainder[0].quantity == Decimal("-1")


def test_campaign_boundary_resolver_uses_carried_campaign_provenance_to_disambiguate_subset() -> None:
    from campaigniq.domain.campaign_boundary_resolver import CampaignBoundaryResolver

    option = Instrument("AMT-155C")
    equity = Instrument("AMT")

    book = LotBook()

    book.seed(
        Lot(
            lot_id="JULY-AMT-CALL",
            instrument=option,
            quantity=Decimal("-1"),
            opened_at=datetime(2026, 7, 17, 11, 52, 55),
            basis_total=None,
            basis_source=None,
            campaign_id="CAMP-JULY-AMT",
        )
    )

    book.seed(
        Lot(
            lot_id="OLDER-AMT-EQUITY",
            instrument=equity,
            quantity=Decimal("100"),
            opened_at=datetime(2026, 6, 12),
            basis_total=None,
            basis_source="HISTORICAL_TRADE_RECONSTRUCTION",
            campaign_id="CAMP-OLDER-AMT",
        )
    )

    book.seed(
        Lot(
            lot_id="JULY-AMT-EQUITY",
            instrument=equity,
            quantity=Decimal("100"),
            opened_at=datetime(2026, 6, 23, 10, 54, 22),
            basis_total=None,
            basis_source=None,
            campaign_id="CAMP-JULY-AMT",
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-AUGUST-AMT",
        trades=(
            Trade(
                legs=(
                    Leg(
                        instrument=option,
                        side=Side.BUY,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            Execution(
                                quantity=Decimal("1"),
                                execution_price=Decimal("10"),
                                executed_at=datetime(
                                    2026, 8, 3, 10, 0
                                ),
                            ),
                        ),
                    ),
                    Leg(
                        instrument=equity,
                        side=Side.SELL,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            Execution(
                                quantity=Decimal("100"),
                                execution_price=Decimal("200"),
                                executed_at=datetime(
                                    2026, 8, 3, 10, 0
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        started_before_data=True,
    )

    resolver = CampaignBoundaryResolver(book)

    assignments = resolver.resolve(
        campaign,
        allow_partial=True,
    )

    assert assignments == {
        "JULY-AMT-CALL": "CAMP-AUGUST-AMT",
        "JULY-AMT-EQUITY": "CAMP-AUGUST-AMT",
    }

    option_lot = book.lots(option)[0]
    equity_lots = book.lots(equity)

    assert option_lot.campaign_id == "CAMP-AUGUST-AMT"

    july_equity = next(
        lot
        for lot in equity_lots
        if lot.lot_id == "JULY-AMT-EQUITY"
    )
    older_equity = next(
        lot
        for lot in equity_lots
        if lot.lot_id == "OLDER-AMT-EQUITY"
    )

    assert july_equity.campaign_id == "CAMP-AUGUST-AMT"
    assert older_equity.campaign_id == "CAMP-OLDER-AMT"



def test_campaign_boundary_resolver_keeps_equal_subsets_ambiguous_without_provenance_anchor() -> None:
    from campaigniq.domain.campaign_boundary_resolver import CampaignBoundaryResolver

    instrument = Instrument("IBM")

    book = LotBook()

    book.seed(
        Lot(
            lot_id="IBM-LOT-ONE",
            instrument=instrument,
            quantity=Decimal("100"),
            opened_at=datetime(2026, 6, 1),
            basis_total=None,
            basis_source=None,
            campaign_id="CAMP-OLD-ONE",
        )
    )

    book.seed(
        Lot(
            lot_id="IBM-LOT-TWO",
            instrument=instrument,
            quantity=Decimal("100"),
            opened_at=datetime(2026, 6, 2),
            basis_total=None,
            basis_source=None,
            campaign_id="CAMP-OLD-TWO",
        )
    )

    campaign = Campaign(
        campaign_id="CAMP-NEW",
        trades=(
            Trade(
                legs=(
                    Leg(
                        instrument=instrument,
                        side=Side.SELL,
                        position_effect=PositionEffect.CLOSE,
                        executions=(
                            Execution(
                                quantity=Decimal("100"),
                                execution_price=Decimal("200"),
                                executed_at=datetime(
                                    2026, 8, 1, 10, 0
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        started_before_data=True,
    )

    resolver = CampaignBoundaryResolver(book)

    assert resolver.candidate_assignments(
        campaign,
        allow_partial=True,
    ) == {}

    assert resolver.resolve(
        campaign,
        allow_partial=True,
    ) == {}

    assert (
        book.lots(instrument)[0].campaign_id
        == "CAMP-OLD-ONE"
    )
    assert (
        book.lots(instrument)[1].campaign_id
        == "CAMP-OLD-TWO"
    )
