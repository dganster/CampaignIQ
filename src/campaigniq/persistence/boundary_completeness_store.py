"""Durable historical-completeness metadata for a finalized month."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from campaigniq.domain.boundary_reconstruction import BoundaryReconstruction
from campaigniq.domain.boundary_validation import HistoricalRequirement

_FORMAT = "campaigniq.boundary_completeness"
_VERSION = 1


@dataclass(frozen=True, slots=True)
class PersistedBoundaryCompleteness:
    """Historical completeness known when one month was finalized."""

    period_start: date
    period_end: date
    status: str
    unresolved_positions: tuple[str, ...]
    unresolved_campaigns: tuple[str, ...]
    historical_requirements: tuple[HistoricalRequirement, ...]

    @property
    def complete(self) -> bool:
        return self.status == "COMPLETE"


def serialize_boundary_completeness(
    *,
    period_start: date,
    period_end: date,
    reconstruction: BoundaryReconstruction,
) -> str:
    """Serialize deterministic completeness metadata independently of storage I/O."""
    incomplete = bool(
        reconstruction.unresolved_positions
        or reconstruction.unresolved_campaigns
        or reconstruction.historical_requirements
    )

    payload = {
        "format": _FORMAT,
        "version": _VERSION,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "status": "PARTIAL" if incomplete else "COMPLETE",
        "unresolved_positions": list(reconstruction.unresolved_positions),
        "unresolved_campaigns": list(reconstruction.unresolved_campaigns),
        "historical_requirements": [
            {
                "case_id": item.case_id,
                "earliest_unresolved_date": item.earliest_unresolved_date.isoformat(),
                "months": list(item.months),
                "document_types": list(item.document_types),
                "reason": item.reason,
            }
            for item in reconstruction.historical_requirements
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def deserialize_boundary_completeness(
    text: str,
) -> PersistedBoundaryCompleteness:
    """Deserialize and validate persisted completeness metadata."""
    payload = json.loads(text)

    if payload.get("format") != _FORMAT:
        raise ValueError("Unsupported boundary completeness persistence format.")
    if payload.get("version") != _VERSION:
        raise ValueError(
            "Unsupported boundary completeness persistence version: "
            f"{payload.get('version')!r}"
        )

    status = payload.get("status")
    if status not in {"COMPLETE", "PARTIAL"}:
        raise ValueError(f"Unsupported boundary completeness status: {status!r}")

    requirements = payload.get("historical_requirements")
    if not isinstance(requirements, list):
        raise ValueError(
            "Boundary completeness payload must contain a "
            "'historical_requirements' list."
        )

    return PersistedBoundaryCompleteness(
        period_start=date.fromisoformat(payload["period_start"]),
        period_end=date.fromisoformat(payload["period_end"]),
        status=status,
        unresolved_positions=tuple(payload.get("unresolved_positions", ())),
        unresolved_campaigns=tuple(payload.get("unresolved_campaigns", ())),
        historical_requirements=tuple(
            HistoricalRequirement(
                case_id=item["case_id"],
                earliest_unresolved_date=date.fromisoformat(
                    item["earliest_unresolved_date"]
                ),
                months=tuple(item["months"]),
                document_types=tuple(item["document_types"]),
                reason=item["reason"],
            )
            for item in requirements
        ),
    )
