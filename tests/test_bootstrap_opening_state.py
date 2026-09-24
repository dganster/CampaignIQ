from pathlib import Path

from campaigniq.import_contract import MonthlyInputRole, monthly_import_contract
from campaigniq.import_preflight import prepare_monthly_import

DATA = Path("tests/data")


def test_contract_exposes_optional_bootstrap_opening_snapshot() -> None:
    contract = monthly_import_contract(2026, 1)

    requirement = next(
        item
        for item in contract.requirements
        if item.role is MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT
    )

    assert requirement.required is False
    assert requirement.user_supplied is True


def test_preflight_bootstraps_opening_state_from_prior_month_snapshot(
    tmp_path: Path,
) -> None:
    supplied = {
        MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT: DATA
        / "schwab/december_positions.txt",
    }

    preflight = prepare_monthly_import(
        2026,
        1,
        authoritative_state_root=tmp_path,
        supplied_inputs=supplied,
    )

    opening = preflight.validation_for(MonthlyInputRole.OPENING_STATE)

    assert opening.valid is True
    assert preflight.opening_state is None
    assert preflight.opening_lot_book is not None


def test_missing_predecessor_requests_prior_month_statement(
    tmp_path: Path,
) -> None:
    preflight = prepare_monthly_import(
        2026,
        1,
        authoritative_state_root=tmp_path,
        supplied_inputs={},
    )

    opening = preflight.validation_for(MonthlyInputRole.OPENING_STATE)

    assert opening.valid is False
    assert "prior month-end Schwab Brokerage Statement" in opening.message
