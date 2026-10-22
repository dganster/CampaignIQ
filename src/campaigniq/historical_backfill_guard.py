"""Fail-closed guard for historical monthly publication.

This is a safety gate, not a backfill publisher. It makes no database writes.
"""
from dataclasses import dataclass
from datetime import date

@dataclass(frozen=True)
class BackfillAssessment:
    blocked: bool
    later_periods: tuple[date, ...]
    existing_period: bool
    message: str


def assess_backfill(period_end: date, authoritative_periods) -> BackfillAssessment:
    periods = tuple(sorted(set(authoritative_periods)))
    later = tuple(p for p in periods if p > period_end)
    existing = period_end in periods
    if existing:
        message = (
            f"Publication blocked: {period_end:%B %Y} is already authoritative. "
            "Replacing a published month requires a separate audited workflow."
        )
    elif later:
        message = (
            f"Historical backfill blocked: {period_end:%B %Y} precedes "
            f"{len(later)} already-published month(s) "
            f"({later[0]:%B %Y} through {later[-1]:%B %Y}). "
            "Review downstream dependencies and create a verified backup before "
            "using a separately authorized backfill publisher."
        )
    else:
        message = "No existing or later published month detected."
    return BackfillAssessment(bool(existing or later), later, existing, message)
