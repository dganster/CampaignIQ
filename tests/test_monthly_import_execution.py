from datetime import date, datetime
from pathlib import Path

import pytest

import campaigniq.monthly_import_execution as execution_module
from campaigniq.closing_inventory_reconciliation import (
    ClosingInventoryMismatch,
    ClosingInventoryReconciliation,
)
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.import_contract import MonthlyInputRole
from campaigniq.import_preflight import prepare_monthly_import
from campaigniq.import_pipeline import PeriodImportPipeline
from campaigniq.persistence.authoritative_lot_state import (
    lot_state_path,
    save_authoritative_lot_state,
)
from campaigniq.domain.lot_book import LotBook
from decimal import Decimal


DATA = Path("tests/data")


def _august_inputs(tmp_path: Path) -> dict[MonthlyInputRole, Path]:
    return {
        MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT: _august_forex_report(tmp_path),
        MonthlyInputRole.THINKORSWIM_TRADE_HISTORY:
            DATA / "thinkorswim" / "Account Trade History August 2026.csv",
        MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS:
            DATA / "schwab" / "august_realized_gain_loss.txt",
        MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT:
            DATA / "schwab" / "august_positions.txt",
        MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE:
            DATA / "schwab" / "august_assignments.txt",
    }


def test_execution_refuses_unready_preflight(tmp_path) -> None:
    preflight = prepare_monthly_import(
        2026,
        8,
        authoritative_state_root=tmp_path,
        supplied_inputs={},
    )

    with pytest.raises(ValueError, match="preflight is not ready"):
        execution_module.execute_monthly_import(
            preflight,
            authoritative_state_root=tmp_path,
            supplied_inputs={},
        )


def test_reconciliation_failure_does_not_persist_authoritative_state(
    tmp_path,
    monkeypatch,
) -> None:
    july = PeriodImportPipeline().run(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        thinkorswim_trade_history=(
            DATA / "thinkorswim" / "Account Trade History July 2026.csv"
        ),
        opening_snapshot=DATA / "schwab" / "june_positions.txt",
        opening_snapshot_at=datetime(2026, 6, 30, 23, 59, 59),
        assignment_lines=(
            (DATA / "schwab" / "july_assignments.txt").read_text().splitlines(),
        ),
    )
    save_authoritative_lot_state(
        tmp_path,
        period_end=date(2026, 7, 31),
        lot_book=july.ending_lot_book,
    )
    inputs = _august_inputs(tmp_path)
    preflight = prepare_monthly_import(
        2026,
        8,
        authoritative_state_root=tmp_path,
        supplied_inputs=inputs,
    )
    assert preflight.ready

    mismatch = ClosingInventoryMismatch(
        instrument=Instrument("IBM"),
        computed_quantity=Decimal("0"),
        snapshot_quantity=Decimal("100"),
    )
    monkeypatch.setattr(
        execution_module,
        "reconcile_closing_inventory",
        lambda **_: ClosingInventoryReconciliation((mismatch,)),
    )

    outcome = execution_module.execute_monthly_import(
        preflight,
        authoritative_state_root=tmp_path,
        supplied_inputs=inputs,
    )

    assert outcome.finalized is False
    assert outcome.authoritative_state_path is None
    assert not lot_state_path(
        tmp_path,
        period_end=date(2026, 8, 31),
    ).exists()


def test_successful_reconciliation_persists_ending_state(
    tmp_path,
    monkeypatch,
) -> None:
    july = PeriodImportPipeline().run(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        thinkorswim_trade_history=(
            DATA / "thinkorswim" / "Account Trade History July 2026.csv"
        ),
        opening_snapshot=DATA / "schwab" / "june_positions.txt",
        opening_snapshot_at=datetime(2026, 6, 30, 23, 59, 59),
        assignment_lines=(
            (DATA / "schwab" / "july_assignments.txt").read_text().splitlines(),
        ),
    )
    save_authoritative_lot_state(
        tmp_path,
        period_end=date(2026, 7, 31),
        lot_book=july.ending_lot_book,
    )
    inputs = _august_inputs(tmp_path)
    preflight = prepare_monthly_import(
        2026,
        8,
        authoritative_state_root=tmp_path,
        supplied_inputs=inputs,
    )
    assert preflight.ready

    monkeypatch.setattr(
        execution_module,
        "reconcile_closing_inventory",
        lambda **_: ClosingInventoryReconciliation(()),
    )

    outcome = execution_module.execute_monthly_import(
        preflight,
        authoritative_state_root=tmp_path,
        supplied_inputs=inputs,
    )

    expected = lot_state_path(
        tmp_path,
        period_end=date(2026, 8, 31),
    )
    assert outcome.finalized is True
    assert outcome.authoritative_state_path == expected
    assert expected.is_file()


def test_assignment_evidence_is_sent_to_period_and_boundary_channels(
    tmp_path,
    monkeypatch,
) -> None:
    july = PeriodImportPipeline().run(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        thinkorswim_trade_history=(
            DATA / "thinkorswim" / "Account Trade History July 2026.csv"
        ),
        opening_snapshot=DATA / "schwab" / "june_positions.txt",
        opening_snapshot_at=datetime(2026, 6, 30, 23, 59, 59),
        assignment_lines=(
            (DATA / "schwab" / "july_assignments.txt").read_text().splitlines(),
        ),
    )
    save_authoritative_lot_state(
        tmp_path,
        period_end=date(2026, 7, 31),
        lot_book=july.ending_lot_book,
    )
    inputs = _august_inputs(tmp_path)
    preflight = prepare_monthly_import(
        2026,
        8,
        authoritative_state_root=tmp_path,
        supplied_inputs=inputs,
    )
    assert preflight.ready

    captured = {}
    real_run = execution_module.PeriodImportPipeline.run

    def capture_run(self, **kwargs):
        captured.update(kwargs)
        return real_run(self, **kwargs)

    monkeypatch.setattr(
        execution_module.PeriodImportPipeline,
        "run",
        capture_run,
    )
    monkeypatch.setattr(
        execution_module,
        "reconcile_closing_inventory",
        lambda **_: ClosingInventoryReconciliation(()),
    )

    execution_module.execute_monthly_import(
        preflight,
        authoritative_state_root=tmp_path,
        supplied_inputs=inputs,
    )

    assert (
        Path(captured["forex_transaction_report"])
        == inputs[MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT]
    )
    assert len(captured["assignment_lines"]) == 1
    assert len(captured["boundary_assignment_lines"]) == 1
    assert (
        captured["assignment_lines"][0]
        == captured["boundary_assignment_lines"][0]
    )


def _august_forex_report(tmp_path: Path) -> Path:
    path = tmp_path / "august_forex_transaction_report.csv"
    path.write_text(
        '"Transaction Report since Jul 31, 2026 16:00:00 (EDT) through Aug 31, 2026 16:00:00 (EDT)"\n'
        '"MTD Settled PL, USD:",+3.00\n'
        '"MTD fee, USD:",0.00\n'
    )
    return path


def test_successful_reconciliation_persists_realized_and_forex_attributions(
    tmp_path,
    monkeypatch,
) -> None:
    july = PeriodImportPipeline().run(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        thinkorswim_trade_history=(
            DATA / "thinkorswim" / "Account Trade History July 2026.csv"
        ),
        opening_snapshot=DATA / "schwab" / "june_positions.txt",
        opening_snapshot_at=datetime(2026, 6, 30, 23, 59, 59),
        assignment_lines=(
            (DATA / "schwab" / "july_assignments.txt").read_text().splitlines(),
        ),
    )
    save_authoritative_lot_state(
        tmp_path,
        period_end=date(2026, 7, 31),
        lot_book=july.ending_lot_book,
    )

    inputs = _august_inputs(tmp_path)
    preflight = prepare_monthly_import(
        2026,
        8,
        authoritative_state_root=tmp_path,
        supplied_inputs=inputs,
    )
    assert preflight.ready

    monkeypatch.setattr(
        execution_module,
        "reconcile_closing_inventory",
        lambda **_: ClosingInventoryReconciliation(()),
    )

    outcome = execution_module.execute_monthly_import(
        preflight,
        authoritative_state_root=tmp_path,
        supplied_inputs=inputs,
    )

    realized_path = tmp_path / "2026-08-realized-attributions.json"
    forex_path = tmp_path / "2026-08-forex-settlement-attributions.json"

    assert outcome.finalized is True
    assert realized_path.is_file()
    assert forex_path.is_file()

    from campaigniq.persistence.realized_attribution_store import (
        load_realized_attributions,
    )
    from campaigniq.persistence.forex_settlement_attribution_store import (
        load_forex_settlement_attributions,
    )

    realized = load_realized_attributions(realized_path)
    forex = load_forex_settlement_attributions(forex_path)

    assert realized.period_start == date(2026, 8, 1)
    assert realized.period_end == date(2026, 8, 31)
    assert realized.attributions

    assert forex.period_start == date(2026, 8, 1)
    assert forex.period_end == date(2026, 8, 31)
    assert forex.attributions == outcome.result.forex_settlement_attributions


def test_failed_reconciliation_does_not_persist_attribution_artifacts(
    tmp_path,
    monkeypatch,
) -> None:
    july = PeriodImportPipeline().run(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        thinkorswim_trade_history=(
            DATA / "thinkorswim" / "Account Trade History July 2026.csv"
        ),
        opening_snapshot=DATA / "schwab" / "june_positions.txt",
        opening_snapshot_at=datetime(2026, 6, 30, 23, 59, 59),
        assignment_lines=(
            (DATA / "schwab" / "july_assignments.txt").read_text().splitlines(),
        ),
    )
    save_authoritative_lot_state(
        tmp_path,
        period_end=date(2026, 7, 31),
        lot_book=july.ending_lot_book,
    )

    inputs = _august_inputs(tmp_path)
    preflight = prepare_monthly_import(
        2026,
        8,
        authoritative_state_root=tmp_path,
        supplied_inputs=inputs,
    )
    assert preflight.ready

    mismatch = ClosingInventoryMismatch(
        instrument=Instrument("IBM"),
        computed_quantity=Decimal("0"),
        snapshot_quantity=Decimal("100"),
    )
    monkeypatch.setattr(
        execution_module,
        "reconcile_closing_inventory",
        lambda **_: ClosingInventoryReconciliation((mismatch,)),
    )

    outcome = execution_module.execute_monthly_import(
        preflight,
        authoritative_state_root=tmp_path,
        supplied_inputs=inputs,
    )

    assert outcome.finalized is False
    assert not (tmp_path / "2026-08-realized-attributions.json").exists()
    assert not (tmp_path / "2026-08-forex-settlement-attributions.json").exists()

def test_successful_monthly_execution_persists_nonempty_forex_attribution(
    tmp_path,
    monkeypatch,
) -> None:
    july = PeriodImportPipeline().run(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        thinkorswim_trade_history=DATA / "thinkorswim" / "Account Trade History July 2026.csv",
        opening_snapshot=DATA / "schwab" / "june_positions.txt",
        opening_snapshot_at=datetime(2026, 6, 30, 23, 59, 59),
        assignment_lines=(DATA / "schwab" / "july_assignments.txt").read_text().splitlines(),
    )
    save_authoritative_lot_state(
        tmp_path, period_end=date(2026, 7, 31), lot_book=july.ending_lot_book
    )

    inputs = _august_inputs(tmp_path)
    inputs[MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT].write_text(
        '"Transaction Report since Jul 31, 2026 16:00:00 (EDT) through Aug 31, 2026 16:00:00 (EDT)"\n'
        '"MTD Settled PL, USD:",+3.00\n'
        '"MTD fee, USD:",0.00\n'
        '="1007745626983","Aug 27, 2026 20:19:57","Aug 28, 2026 17:00:00",settlement,EUR/USD,Sell,1.16508,"-100,000","+116,508",0.00,,"+3.00 USD",0,,"+3.00 USD"\n',
        encoding="utf-8",
    )

    original = inputs[MonthlyInputRole.THINKORSWIM_TRADE_HISTORY]
    synthetic = tmp_path / "august_trade_history_with_forex.csv"
    synthetic.write_text(
        original.read_text(encoding="utf-8")
        + '\n08/20/26 10:00:00,TRD,BUY,100000,TO OPEN,EUR/USD,1.16000\n'
        + '08/27/26 18:19:57,TRD,SELL,100000,TO CLOSE,EUR/USD,1.16508\n',
        encoding="utf-8",
    )
    inputs[MonthlyInputRole.THINKORSWIM_TRADE_HISTORY] = synthetic

    preflight = prepare_monthly_import(
        2026, 8, authoritative_state_root=tmp_path, supplied_inputs=inputs
    )
    assert preflight.ready
    monkeypatch.setattr(
        execution_module,
        "reconcile_closing_inventory",
        lambda **_: ClosingInventoryReconciliation(()),
    )

    outcome = execution_module.execute_monthly_import(
        preflight, authoritative_state_root=tmp_path, supplied_inputs=inputs
    )

    from campaigniq.persistence.forex_settlement_attribution_store import (
        load_forex_settlement_attributions,
    )

    persisted = load_forex_settlement_attributions(
        tmp_path / "2026-08-forex-settlement-attributions.json"
    )
    assert outcome.finalized is True
    assert len(outcome.result.forex_settlement_attributions) == 1
    attribution = outcome.result.forex_settlement_attributions[0]
    assert attribution.settlement.instrument == "EUR/USD"
    assert attribution.gain_loss == Decimal("3.00")
    assert persisted.attributions == (attribution,)

def test_monthly_execution_reports_nonzero_forex_control_delta_without_blocking_finalization(
    tmp_path,
    monkeypatch,
) -> None:
    july = PeriodImportPipeline().run(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        thinkorswim_trade_history=DATA / "thinkorswim" / "Account Trade History July 2026.csv",
        opening_snapshot=DATA / "schwab" / "june_positions.txt",
        opening_snapshot_at=datetime(2026, 6, 30, 23, 59, 59),
        assignment_lines=(DATA / "schwab" / "july_assignments.txt").read_text().splitlines(),
    )
    save_authoritative_lot_state(
        tmp_path, period_end=date(2026, 7, 31), lot_book=july.ending_lot_book
    )

    inputs = _august_inputs(tmp_path)
    inputs[MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT].write_text(
        '"Transaction Report since Jul 31, 2026 16:00:00 (EDT) through Aug 31, 2026 16:00:00 (EDT)"\n'
        '"MTD Settled PL, USD:",+3.27\n'
        '"MTD fee, USD:",0.00\n'
        '="1007745626983","Aug 27, 2026 20:19:57","Aug 28, 2026 17:00:00",settlement,EUR/USD,Sell,1.16508,"-100,000","+116,508",0.00,,"+3.00 USD",0,,"+3.00 USD"\n',
        encoding="utf-8",
    )

    original = inputs[MonthlyInputRole.THINKORSWIM_TRADE_HISTORY]
    synthetic = tmp_path / "august_trade_history_forex_control_delta.csv"
    synthetic.write_text(
        original.read_text(encoding="utf-8")
        + '\n08/20/26 10:00:00,TRD,BUY,100000,TO OPEN,EUR/USD,1.16000\n'
        + '08/27/26 18:19:57,TRD,SELL,100000,TO CLOSE,EUR/USD,1.16508\n',
        encoding="utf-8",
    )
    inputs[MonthlyInputRole.THINKORSWIM_TRADE_HISTORY] = synthetic

    preflight = prepare_monthly_import(
        2026, 8, authoritative_state_root=tmp_path, supplied_inputs=inputs
    )
    assert preflight.ready
    monkeypatch.setattr(
        execution_module,
        "reconcile_closing_inventory",
        lambda **_: ClosingInventoryReconciliation(()),
    )

    outcome = execution_module.execute_monthly_import(
        preflight, authoritative_state_root=tmp_path, supplied_inputs=inputs
    )

    assert outcome.finalized is True
    assert outcome.forex_settlement_control_delta_usd == Decimal("0.27")
    assert outcome.forex_settlement_control_reconciled is False
    assert len(outcome.result.forex_settlement_attributions) == 1
    assert outcome.result.forex_settlement_attributions[0].gain_loss == Decimal("3.00")
