from datetime import date
from pathlib import Path

from campaigniq.domain.boundary_validation import HistoricalRequirement
from campaigniq.domain.historical_evidence_resolver import (
    HistoricalEvidenceResolver,
)
from campaigniq.domain.historical_evidence import (
    ThinkorswimHistoricalEvidenceRepository,
)


DATA = Path("tests/data/thinkorswim")


def _repository() -> ThinkorswimHistoricalEvidenceRepository:
    return ThinkorswimHistoricalEvidenceRepository(DATA)


def test_resolves_december_2025_trade_history() -> None:
    requirement = HistoricalRequirement(
        case_id="TEST-DECEMBER",
        earliest_unresolved_date=date(2025, 12, 1),
        months=("2025-12",),
        document_types=("Account Trade History",),
        reason="Historical campaign ancestry is unresolved.",
    )

    resolution = HistoricalEvidenceResolver(_repository()).resolve(
        requirement
    )

    assert resolution.complete
    assert len(resolution.trade_history) == 1
    assert (
        resolution.trade_history[0].path.name
        == "Account Trade History December 2025.csv"
    )


def test_resolves_multiple_historical_months() -> None:
    requirement = HistoricalRequirement(
        case_id="TEST-MULTI",
        earliest_unresolved_date=date(2025, 11, 1),
        months=("2025-11", "2025-12"),
        document_types=("Account Trade History",),
        reason="Historical campaign ancestry is unresolved.",
    )

    resolution = HistoricalEvidenceResolver(_repository()).resolve(
        requirement
    )

    assert resolution.complete
    assert len(resolution.trade_history) == 2
    assert [item.path.name for item in resolution.trade_history] == [
        "Account Trade History November 2025.csv",
        "Account Trade History December 2025.csv",
    ]


def test_reports_incomplete_when_requested_month_is_missing() -> None:
    requirement = HistoricalRequirement(
        case_id="TEST-MISSING",
        earliest_unresolved_date=date(2024, 10, 1),
        months=("2024-10",),
        document_types=("Account Trade History",),
        reason="Historical campaign ancestry is unresolved.",
    )

    resolution = HistoricalEvidenceResolver(_repository()).resolve(
        requirement
    )

    assert not resolution.complete
    assert resolution.trade_history == ()


def test_does_not_claim_brokerage_statement_is_resolved() -> None:
    requirement = HistoricalRequirement(
        case_id="TEST-STATEMENT",
        earliest_unresolved_date=date(2025, 12, 1),
        months=("2025-12",),
        document_types=("Brokerage Statement",),
        reason="Historical opening inventory is unresolved.",
    )

    resolution = HistoricalEvidenceResolver(_repository()).resolve(
        requirement
    )

    assert not resolution.complete
    assert resolution.trade_history == ()
