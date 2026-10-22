from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType


def test_march_2025_spx_expired_residuals_are_identifiable():
    book = LotBook()

    residuals = (
        (26, "5790", OptionType.CALL, "-10"),
        (26, "5800", OptionType.CALL, "10"),
        (27, "5675", OptionType.PUT, "-10"),
        (27, "5685", OptionType.PUT, "10"),
        (27, "5730", OptionType.CALL, "10"),
        (27, "5740", OptionType.CALL, "-10"),
    )

    for index, (day, strike, option_type, quantity) in enumerate(
        residuals, start=1
    ):
        contract = OptionContract(
            underlying="SPX",
            expiration=date(2025, 3, day),
            strike=Decimal(strike),
            option_type=option_type,
        )
        book.seed(
            Lot(
                lot_id=f"march-spx-{index}",
                instrument=contract,
                quantity=Decimal(quantity),
                opened_at=datetime(2025, 3, day, 9, 0),
                basis_total=None,
                campaign_id=None,
            )
        )

    outstanding = {
        instrument: sum(
            (lot.quantity for lot in book.lots(instrument)),
            Decimal("0"),
        )
        for instrument in book.instruments()
    }

    assert len(outstanding) == 6
    assert sorted(outstanding.values()) == [
        Decimal("-10"),
        Decimal("-10"),
        Decimal("-10"),
        Decimal("10"),
        Decimal("10"),
        Decimal("10"),
    ]


def test_expiration_changes_close_residuals_without_creating_new_lots():
    book = LotBook()
    residuals = (
        (26, "5790", OptionType.CALL, "-10"),
        (26, "5800", OptionType.CALL, "10"),
        (27, "5675", OptionType.PUT, "-10"),
        (27, "5685", OptionType.PUT, "10"),
        (27, "5730", OptionType.CALL, "10"),
        (27, "5740", OptionType.CALL, "-10"),
    )

    contracts = []

    for index, (day, strike, option_type, quantity) in enumerate(
        residuals, start=1
    ):
        contract = OptionContract(
            underlying="SPX",
            expiration=date(2025, 3, day),
            strike=Decimal(strike),
            option_type=option_type,
        )
        contracts.append(contract)
        book.seed(
            Lot(
                lot_id=f"residual-{index}",
                instrument=contract,
                quantity=Decimal(quantity),
                opened_at=datetime(2025, 3, day, 9),
                basis_total=None,
                campaign_id=None,
            )
        )

    unrelated = OptionContract(
        underlying="SPX",
        expiration=date(2025, 4, 4),
        strike=Decimal("6000"),
        option_type=OptionType.CALL,
    )
    book.seed(
        Lot(
            lot_id="unrelated",
            instrument=unrelated,
            quantity=Decimal("-2"),
            opened_at=datetime(2025, 3, 27, 9),
            basis_total=None,
            campaign_id=None,
        )
    )

    for contract in contracts:
        remaining = sum(
            (lot.quantity for lot in book.lots(contract)),
            Decimal("0"),
        )
        book.apply_signed_change(
            instrument=contract,
            quantity=-remaining,
            occurred_at=datetime(2025, 3, 31, 23, 59),
        )

    assert all(book.lots(contract) == () for contract in contracts)
    assert sum(
        (lot.quantity for lot in book.lots(unrelated)),
        Decimal("0"),
    ) == Decimal("-2")


def test_reconciler_identifies_march_spx_residual_with_broker_evidence():
    from campaigniq.domain.expired_option_reconciler import (
        identify_expired_option_candidates,
    )
    from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord

    contract = OptionContract(
        underlying="SPX",
        expiration=date(2025, 3, 26),
        strike=Decimal("5790"),
        option_type=OptionType.CALL,
    )

    book = LotBook()
    book.seed(
        Lot(
            lot_id="march-5790-call",
            instrument=contract,
            quantity=Decimal("-10"),
            opened_at=datetime(2025, 3, 26, 7, 48),
            basis_total=None,
            campaign_id=None,
        )
    )

    evidence = RealizedGainLossRecord(
        closed_date=date(2025, 3, 26),
        instrument=contract,
        quantity=Decimal("10"),
        closing_price=Decimal("4.52"),
        proceeds=Decimal("4517.79"),
        cost_basis=Decimal("0"),
        gain_loss=Decimal("4517.79"),
        basis_method="FIFO",
        term="SHORT",
    )

    candidates = identify_expired_option_candidates(
        ending_lot_book=book,
        realized_gain_loss=(evidence,),
        period_end=date(2025, 3, 31),
    )

    assert len(candidates) == 1
    assert candidates[0].contract == contract
    assert candidates[0].residual_quantity == Decimal("-10")
    assert candidates[0].reported_disposition_quantity == Decimal("10")

    # Identification must not modify the accounting ledger.
    assert sum(
        (lot.quantity for lot in book.lots(contract)),
        Decimal("0"),
    ) == Decimal("-10")


def test_schwab_spxw_matches_thinkorswim_spx():
    from campaigniq.domain.expired_option_reconciler import (
        identify_expired_option_candidates,
    )
    from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord

    def contract(symbol):
        return OptionContract(
            underlying=symbol,
            expiration=date(2025, 3, 26),
            strike=Decimal("5790"),
            option_type=OptionType.CALL,
        )

    book = LotBook()
    book.seed(
        Lot(
            lot_id="spx-short-call",
            instrument=contract("SPX"),
            quantity=Decimal("-10"),
            opened_at=datetime(2025, 3, 26, 7, 48),
            basis_total=None,
            campaign_id=None,
        )
    )

    schwab_record = RealizedGainLossRecord(
        closed_date=date(2025, 3, 26),
        instrument=contract("SPXW"),
        quantity=Decimal("10"),
        closing_price=Decimal("4.52"),
        proceeds=Decimal("4517.79"),
        cost_basis=Decimal("0"),
        gain_loss=Decimal("4517.79"),
        basis_method="FIFO",
        term="SHORT",
    )

    candidates = identify_expired_option_candidates(
        ending_lot_book=book,
        realized_gain_loss=(schwab_record,),
        period_end=date(2025, 3, 31),
    )

    assert len(candidates) == 1
    assert candidates[0].contract == contract("SPX")
    assert candidates[0].residual_quantity == Decimal("-10")
    assert candidates[0].reported_disposition_quantity == Decimal("10")

    # Broker source evidence must remain unchanged.
    assert schwab_record.instrument.underlying == "SPXW"
    assert sum(
        (lot.quantity for lot in book.lots(contract("SPX"))),
        Decimal("0"),
    ) == Decimal("-10")


def test_march_27_disposition_coverage():
    from campaigniq.domain.expired_option_reconciler import (
        calculate_disposition_coverage,
    )

    for strike, option_type in (
        ("5730", OptionType.CALL),
        ("5740", OptionType.CALL),
        ("5675", OptionType.PUT),
        ("5685", OptionType.PUT),
    ):
        contract = OptionContract(
            underlying="SPX",
            expiration=date(2025, 3, 27),
            strike=Decimal(strike),
            option_type=option_type,
        )

        coverage = calculate_disposition_coverage(
            contract=contract,
            reported_quantity=Decimal("20"),
            executed_closing_quantity=Decimal("10"),
        )

        assert coverage.reported_quantity == Decimal("20")
        assert coverage.executed_closing_quantity == Decimal("10")
        assert coverage.unexplained_quantity == Decimal("10")


def test_disposition_coverage_rejects_overmatching():
    from campaigniq.domain.expired_option_reconciler import (
        calculate_disposition_coverage,
    )

    contract = OptionContract(
        underlying="SPX",
        expiration=date(2025, 3, 27),
        strike=Decimal("5730"),
        option_type=OptionType.CALL,
    )

    import pytest

    with pytest.raises(ValueError, match="exceeds"):
        calculate_disposition_coverage(
            contract=contract,
            reported_quantity=Decimal("10"),
            executed_closing_quantity=Decimal("20"),
        )


def test_executed_closing_quantities_count_partial_fills_only():
    from datetime import datetime

    from campaigniq.domain.execution import Execution
    from campaigniq.domain.leg import Leg
    from campaigniq.domain.position_effect import PositionEffect
    from campaigniq.domain.side import Side
    from campaigniq.domain.trade import Trade
    from campaigniq.domain.expired_option_reconciler import (
        executed_option_closing_quantities,
    )

    contract = OptionContract(
        underlying="SPX",
        expiration=date(2025, 3, 27),
        strike=Decimal("5730"),
        option_type=OptionType.CALL,
    )

    def execution(quantity, second):
        return Execution(
            quantity=Decimal(str(quantity)),
            execution_price=Decimal("0.86"),
            executed_at=datetime(2025, 3, 27, 11, 0, second),
        )

    opening = Leg(
        instrument=contract,
        side=Side.SELL,
        position_effect=PositionEffect.OPEN,
        executions=(execution(10, 0),),
    )

    closing = Leg(
        instrument=contract,
        side=Side.BUY,
        position_effect=PositionEffect.CLOSE,
        executions=tuple(execution(2, i) for i in range(5)),
    )

    opposite_opening = Leg(
        instrument=contract,
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        executions=(execution(10, 5),),
    )

    quantities = executed_option_closing_quantities(
        (
            Trade(legs=(opening,)),
            Trade(legs=(closing,)),
            Trade(legs=(opposite_opening,)),
        )
    )

    assert quantities[contract] == Decimal("10")
