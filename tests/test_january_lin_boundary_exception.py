"""Acceptance regression for the January 2026 LIN boundary discrepancy."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from campaigniq.closing_inventory_reconciliation import (
    reconcile_closing_inventory,
)
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.import_pipeline import PeriodImportPipeline
from campaigniq.importers.schwab.position_snapshot_reader import (
    read_position_snapshot_section,
)
from campaigniq.persistence.reconciliation_decision import (
    ACCEPT_TRANSACTION_DERIVED_STATE,
    BOUNDARY_TIMING_EXCEPTION,
    ReconciliationDecision,
    capture_reconciliation_mismatches,
    decision_exactly_matches_reconciliation,
)


DATA = Path("tests/data")

JANUARY_TRADES = (
    DATA / "thinkorswim/Account Trading History 2026.csv"
)
DECEMBER_POSITIONS = DATA / "schwab/december_positions.txt"
JANUARY_POSITIONS = DATA / "schwab/january_positions.txt"
JANUARY_REALIZED = (
    DATA / "schwab/january_realized_gain_loss.txt"
)
JANUARY_ASSIGNMENTS = DATA / "schwab/january_assignments.txt"


def _january_result():
    assignment_lines = tuple(
        JANUARY_ASSIGNMENTS.read_text(
            encoding="utf-8"
        ).splitlines()
    )

    return PeriodImportPipeline().run(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        thinkorswim_trade_history=JANUARY_TRADES,
        opening_snapshot=DECEMBER_POSITIONS,
        opening_snapshot_at=datetime(2025, 12, 31),
        assignment_lines=(list(assignment_lines),),
        realized_gain_loss_report=JANUARY_REALIZED,
    )


def _january_reconciliation():
    result = _january_result()

    snapshot_rows = read_position_snapshot_section(
        JANUARY_POSITIONS.read_text(
            encoding="utf-8"
        ).splitlines(),
        snapshot_at=datetime(2026, 1, 31),
    )

    reconciliation = reconcile_closing_inventory(
        ending_lot_book=result.ending_lot_book,
        snapshot_rows=snapshot_rows,
        period_end=date(2026, 1, 31),
    )

    return result, reconciliation


def test_january_reconciliation_has_exactly_two_lin_boundary_mismatches():
    result, reconciliation = _january_reconciliation()

    assert result.boundary_reconstruction.unresolved_positions == ()
    assert result.boundary_reconstruction.unresolved_campaigns == ()
    assert result.boundary_reconstruction.historical_requirements == ()

    lin_equity = Instrument("LIN")
    lin_call = OptionContract(
        underlying="LIN",
        expiration=date(2026, 2, 20),
        strike=Decimal("365"),
        option_type=OptionType.CALL,
    )

    actual = {
        mismatch.instrument: (
            mismatch.computed_quantity,
            mismatch.snapshot_quantity,
        )
        for mismatch in reconciliation.mismatches
    }

    assert actual == {
        lin_equity: (
            Decimal("0"),
            Decimal("500"),
        ),
        lin_call: (
            Decimal("0"),
            Decimal("-5"),
        ),
    }

    assert not reconciliation.reconciled


def test_january_lin_boundary_decision_exactly_matches_real_reconciliation():
    _, reconciliation = _january_reconciliation()

    decision = ReconciliationDecision(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        decision_type=BOUNDARY_TIMING_EXCEPTION,
        resolution=ACCEPT_TRANSACTION_DERIVED_STATE,
        reason=(
            "Broker transaction history shows the LIN shares and "
            "Feb 20 2026 $365 calls closed on January 30, while the "
            "January 31 statement snapshot still reports them."
        ),
        evidence=(
            "January 30, 2026 LIN stock sale in broker transaction history",
            (
                "January 30, 2026 buy-to-close of five LIN "
                "Feb 20 2026 $365 calls in broker transaction history"
            ),
        ),
        mismatches=capture_reconciliation_mismatches(
            reconciliation.mismatches
        ),
        approved=True,
    )

    assert decision_exactly_matches_reconciliation(
        decision=decision,
        reconciliation=reconciliation,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )


def test_january_transaction_derived_state_carries_no_lin_position():
    result, _ = _january_reconciliation()

    lin_equity = Instrument("LIN")
    lin_call = OptionContract(
        underlying="LIN",
        expiration=date(2026, 2, 20),
        strike=Decimal("365"),
        option_type=OptionType.CALL,
    )

    quantities = {
        instrument: sum(
            (lot.quantity for lot in lots),
            Decimal("0"),
        )
        for instrument, lots in result.ending_lot_book._lots.items()
    }

    assert quantities.get(lin_equity, Decimal("0")) == Decimal("0")
    assert quantities.get(lin_call, Decimal("0")) == Decimal("0")
