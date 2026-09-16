from pathlib import Path
from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.import_contract import MonthlyInputRole
from campaigniq.import_preflight import prepare_monthly_import
from campaigniq.persistence.authoritative_lot_state import (
    save_authoritative_lot_state,
)


def _august_book() -> LotBook:
    book = LotBook()
    book.seed(
        Lot(
            lot_id="LOT-IBM-001",
            instrument=Instrument("IBM"),
            quantity=Decimal("100"),
            opened_at=datetime(2026, 8, 12, 10, 30),
            basis_total=Decimal("25000"),
            basis_source="SCHWAB_REALIZED_GAIN_LOSS",
            campaign_id="CAMP-000001",
        )
    )
    return book


def test_preflight_reports_missing_required_user_inputs(tmp_path) -> None:
    save_authoritative_lot_state(
        tmp_path,
        period_end=date(2026, 8, 31),
        lot_book=_august_book(),
    )

    result = prepare_monthly_import(
        2026,
        9,
        authoritative_state_root=tmp_path,
        supplied_inputs={},
    )

    assert result.ready is False
    assert result.validation_for(MonthlyInputRole.OPENING_STATE).valid is True
    assert (
        result.validation_for(MonthlyInputRole.THINKORSWIM_TRADE_HISTORY).message
        == "Required input has not been supplied."
    )
    assert (
        result.validation_for(
            MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT
        ).valid
        is False
    )


def test_preflight_exposes_discovered_opening_lot_book(tmp_path) -> None:
    original = _august_book()
    save_authoritative_lot_state(
        tmp_path,
        period_end=date(2026, 8, 31),
        lot_book=original,
    )

    result = prepare_monthly_import(
        2026,
        9,
        authoritative_state_root=tmp_path,
        supplied_inputs={},
    )

    assert result.opening_state is not None
    assert result.opening_state.period_end == date(2026, 8, 31)
    assert result.opening_lot_book is not None
    assert result.opening_lot_book._lots == original._lots
    assert result.opening_lot_book is not original


def test_preflight_reports_missing_predecessor_state(tmp_path) -> None:
    result = prepare_monthly_import(
        2026,
        9,
        authoritative_state_root=tmp_path,
        supplied_inputs={},
    )

    opening = result.validation_for(MonthlyInputRole.OPENING_STATE)
    assert opening.valid is False
    assert "2026-08-31" in opening.message
    assert "reconstruction is required" in opening.message
    assert result.ready is False


def test_optional_inputs_do_not_block_readiness(tmp_path) -> None:
    save_authoritative_lot_state(
        tmp_path,
        period_end=date(2026, 8, 31),
        lot_book=_august_book(),
    )

    result = prepare_monthly_import(
        2026,
        9,
        authoritative_state_root=tmp_path,
        supplied_inputs={},
    )

    assignment = result.validation_for(
        MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE
    )
    historical = result.validation_for(
        MonthlyInputRole.HISTORICAL_TRADE_EVIDENCE
    )
    assert assignment.valid is True
    assert assignment.message == "Optional input not supplied."
    assert historical.valid is True


def test_august_real_files_preflight_as_ready(tmp_path) -> None:
    data = "tests/data"
    save_authoritative_lot_state(
        tmp_path,
        period_end=date(2026, 7, 31),
        lot_book=_august_book(),
    )

    result = prepare_monthly_import(
        2026,
        8,
        authoritative_state_root=tmp_path,
        supplied_inputs={
            MonthlyInputRole.THINKORSWIM_TRADE_HISTORY: (
                f"{data}/thinkorswim/Account Trade History August 2026.csv"
            ),
            MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT: _august_forex_report(tmp_path),
            MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS: (
                f"{data}/schwab/august_realized_gain_loss.txt"
            ),
            MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT: (
                f"{data}/schwab/august_positions.txt"
            ),
            MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE: (
                f"{data}/schwab/august_assignments.txt"
            ),
        },
    )

    assert result.ready is True
    assert result.validation_for(
        MonthlyInputRole.THINKORSWIM_TRADE_HISTORY
    ).record_count > 0
    assert result.validation_for(
        MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS
    ).record_count == 62
    assert result.validation_for(
        MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT
    ).record_count > 0
    assert result.validation_for(
        MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE
    ).valid is True


def _august_forex_report(tmp_path: Path) -> Path:
    path = tmp_path / "august_forex_transaction_report.csv"
    path.write_text(
        '"Transaction Report since Jul 31, 2026 16:00:00 (EDT) through Aug 31, 2026 16:00:00 (EDT)"\n'
        '"MTD Settled PL, USD:",+3.00\n'
        '"MTD fee, USD:",0.00\n'
    )
    return path
