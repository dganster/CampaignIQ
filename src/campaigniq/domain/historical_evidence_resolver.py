"""Resolve historical requirements against available evidence."""

from __future__ import annotations

from dataclasses import dataclass

from campaigniq.domain.boundary_validation import HistoricalRequirement
from campaigniq.domain.historical_evidence import (
    HistoricalEvidence,
    ThinkorswimHistoricalEvidenceRepository,
)


@dataclass(frozen=True, slots=True)
class HistoricalEvidenceResolution:
    """Result of resolving historical trade-history evidence."""

    requirement: HistoricalRequirement
    trade_history: tuple[HistoricalEvidence, ...]

    @property
    def complete(self) -> bool:
        """Return whether trade-history evidence was found for every month."""
        return len(self.trade_history) == len(self.requirement.months)


class HistoricalEvidenceResolver:
    """Resolve historical trade-history requirements."""

    def __init__(
        self,
        repository: ThinkorswimHistoricalEvidenceRepository,
    ) -> None:
        self.repository = repository

    def resolve(
        self,
        requirement: HistoricalRequirement,
    ) -> HistoricalEvidenceResolution:
        """Find trade-history evidence for each requested month."""

        evidence: list[HistoricalEvidence] = []

        if "Account Trade History" not in requirement.document_types:
            return HistoricalEvidenceResolution(
                requirement=requirement,
                trade_history=(),
            )

        for month in requirement.months:
            year, month_number = (
                int(value) for value in month.split("-")
            )

            item = self.repository.find_month(
                year=year,
                month=month_number,
                document_type="Account Trade History",
            )

            if item is not None:
                evidence.append(item)

        return HistoricalEvidenceResolution(
            requirement=requirement,
            trade_history=tuple(evidence),
        )