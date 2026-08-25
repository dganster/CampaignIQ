"""Discover historical source evidence available to CampaignIQ."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True, slots=True)
class HistoricalEvidence:
    """One historical source file and the period it actually covers."""

    path: Path
    document_type: str
    coverage_start: date
    coverage_end: date


class ThinkorswimHistoricalEvidenceRepository:
    """Discover Thinkorswim historical trade-history evidence by date coverage."""

    ACCOUNT_TRADE_HISTORY = "Account Trade History"

    def __init__(self, source_root: str | Path) -> None:
        self.source_root = Path(source_root)

    def available(self) -> tuple[HistoricalEvidence, ...]:
        """Return all discoverable Thinkorswim trade-history sources."""
        evidence: list[HistoricalEvidence] = []

        for path in sorted(self.source_root.glob("*.csv")):
            item = self._inspect(path)
            if item is not None:
                evidence.append(item)

        return tuple(evidence)

    def find(
        self,
        *,
        start: date,
        end: date,
        document_type: str = ACCOUNT_TRADE_HISTORY,
    ) -> HistoricalEvidence | None:
        """Find the narrowest available source covering the requested period."""

        candidates = [
            item
            for item in self.available()
            if item.document_type == document_type
            and item.coverage_start <= start
            and item.coverage_end >= end
        ]

        if not candidates:
            return None

        return min(
            candidates,
            key=lambda item: (
                (item.coverage_end - item.coverage_start).days,
                item.path.name,
            ),
        )
    def find_month(
        self,
        *,
        year: int,
        month: int,
        document_type: str = ACCOUNT_TRADE_HISTORY,
    ) -> HistoricalEvidence | None:
        """Find the narrowest source containing evidence for a calendar month."""

        candidates = [
            item
            for item in self.available()
            if item.document_type == document_type
            and item.coverage_start.year <= year
            and item.coverage_end.year >= year
            and (
                item.coverage_start.year < year
                or item.coverage_start.month <= month
            )
            and (
                item.coverage_end.year > year
                or item.coverage_end.month >= month
            )
        ]

        if not candidates:
            return None

        return min(
            candidates,
            key=lambda item: (
                (item.coverage_end - item.coverage_start).days,
                item.path.name,
            ),
        )

    @classmethod
    def _inspect(cls, path: Path) -> HistoricalEvidence | None:
        """Inspect one source file and determine actual trade-date coverage."""

        from campaigniq.importers.thinkorswim.trade_history_reader import (
            ThinkorswimTradeHistoryReader,
        )
        from campaigniq.sources.thinkorswim.source_reader import (
            ThinkorswimSourceReader,
        )

        try:
            statement = ThinkorswimSourceReader().read(str(path))
            section = statement.section("Account Trade History")
            orders = ThinkorswimTradeHistoryReader().read(section)
        except (OSError, ValueError, KeyError):
            return None

        dates = [
            order.exec_time.date()
            for order in orders
            if order.exec_time is not None
        ]

        if not dates:
            return None

        return HistoricalEvidence(
            path=path,
            document_type=cls.ACCOUNT_TRADE_HISTORY,
            coverage_start=min(dates),
            coverage_end=max(dates),
        )