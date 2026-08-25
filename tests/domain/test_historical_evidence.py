from datetime import date
from pathlib import Path

from campaigniq.domain.historical_evidence import (
    ThinkorswimHistoricalEvidenceRepository,
)


DATA = Path("tests/data/thinkorswim")


def test_discovers_december_2025_trade_history() -> None:
    repository = ThinkorswimHistoricalEvidenceRepository(DATA)

    evidence = repository.find_month(
    year=2025,
    month=12,
)

    assert evidence is not None
    assert evidence.document_type == "Account Trade History"
    assert evidence.path.name == "Account Trade History December 2025.csv"
    assert evidence.coverage_start == date(2025, 12, 2)
    assert evidence.coverage_end == date(2025, 12, 31)

def test_month_lookup_falls_back_to_annual_history() -> None:
    repository = ThinkorswimHistoricalEvidenceRepository(DATA)

    evidence = repository.find_month(
        year=2025,
        month=10,
    )

    assert evidence is not None
    assert evidence.path.name == "Account Trading History 2025.csv"

def test_can_use_annual_history_when_month_specific_file_is_unavailable(
) -> None:
    repository = ThinkorswimHistoricalEvidenceRepository(DATA)

    evidence = repository.find(
        start=date(2025, 10, 1),
        end=date(2025, 10, 31),
    )

    assert evidence is not None
    assert evidence.path.name == "Account Trading History 2025.csv"


def test_missing_month_returns_no_evidence() -> None:
    repository = ThinkorswimHistoricalEvidenceRepository(DATA)

    evidence = repository.find(
        start=date(2024, 10, 1),
        end=date(2024, 10, 31),
    )

    assert evidence is None


def test_brokerage_statement_is_not_falsely_satisfied_by_trade_history(
) -> None:
    repository = ThinkorswimHistoricalEvidenceRepository(DATA)

    evidence = repository.find(
        start=date(2025, 12, 1),
        end=date(2025, 12, 31),
        document_type="Brokerage Statement",
    )

    assert evidence is None
