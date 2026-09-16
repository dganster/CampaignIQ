from datetime import date

from campaigniq.import_contract import (
    MonthlyInputRole,
    monthly_import_contract,
)


def test_september_contract_uses_calendar_month_boundaries() -> None:
    contract = monthly_import_contract(2026, 9)

    assert contract.period_start == date(2026, 9, 1)
    assert contract.period_end == date(2026, 9, 30)


def test_contract_uses_semantic_roles_not_filenames() -> None:
    contract = monthly_import_contract(2026, 9)

    assert tuple(requirement.role for requirement in contract.requirements) == (
        MonthlyInputRole.THINKORSWIM_TRADE_HISTORY,
        MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT,
        MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS,
        MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT,
        MonthlyInputRole.OPENING_STATE,
        MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE,
        MonthlyInputRole.HISTORICAL_TRADE_EVIDENCE,
    )

    descriptions = tuple(
        requirement.description
        for requirement in contract.requirements
    )

    assert all("tests/data/" not in description for description in descriptions)
    assert all(".csv" not in description for description in descriptions)
    assert all(".txt" not in description for description in descriptions)


def test_contract_distinguishes_user_inputs_from_campaigniq_state() -> None:
    contract = monthly_import_contract(2026, 9)

    user_roles = tuple(
        requirement.role
        for requirement in contract.user_supplied_requirements
    )

    assert user_roles == (
        MonthlyInputRole.THINKORSWIM_TRADE_HISTORY,
        MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT,
        MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS,
        MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT,
        MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE,
    )

    by_role = {
        requirement.role: requirement
        for requirement in contract.requirements
    }

    assert by_role[MonthlyInputRole.THINKORSWIM_TRADE_HISTORY].required is True
    assert by_role[MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT].required is True
    assert by_role[MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS].required is True
    assert by_role[MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT].required is True
    assert by_role[MonthlyInputRole.OPENING_STATE].required is True

    assert (
        by_role[MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE].required
        is False
    )
    assert (
        by_role[MonthlyInputRole.HISTORICAL_TRADE_EVIDENCE].required
        is False
    )


def test_contract_handles_leap_year_calendar_boundary() -> None:
    contract = monthly_import_contract(2028, 2)

    assert contract.period_start == date(2028, 2, 1)
    assert contract.period_end == date(2028, 2, 29)
