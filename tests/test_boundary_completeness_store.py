from datetime import date

from campaigniq.domain.boundary_reconstruction import BoundaryReconstruction
from campaigniq.domain.boundary_validation import HistoricalRequirement
from campaigniq.persistence.boundary_completeness_store import (
    deserialize_boundary_completeness,
    serialize_boundary_completeness,
)


def test_complete_boundary_round_trips() -> None:
    text = serialize_boundary_completeness(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        reconstruction=BoundaryReconstruction(
            unresolved_positions=(),
            unresolved_campaigns=(),
            historical_requirements=(),
        ),
    )

    persisted = deserialize_boundary_completeness(text)

    assert persisted.status == "COMPLETE"
    assert persisted.complete
    assert persisted.unresolved_positions == ()
    assert persisted.unresolved_campaigns == ()
    assert persisted.historical_requirements == ()


def test_partial_boundary_round_trips_without_discarding_findings() -> None:
    requirement = HistoricalRequirement(
        case_id="CAMP-000123",
        earliest_unresolved_date=date(2026, 7, 31),
        months=("2026-07",),
        document_types=("Account Trade History", "Brokerage Statement"),
        reason=(
            "The opening position is known, but the supplied history does not "
            "establish when the campaign began for AMZN."
        ),
    )

    text = serialize_boundary_completeness(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        reconstruction=BoundaryReconstruction(
            unresolved_positions=("AMZN",),
            unresolved_campaigns=("CAMP-000123",),
            historical_requirements=(requirement,),
        ),
    )

    persisted = deserialize_boundary_completeness(text)

    assert persisted.status == "PARTIAL"
    assert not persisted.complete
    assert persisted.unresolved_positions == ("AMZN",)
    assert persisted.unresolved_campaigns == ("CAMP-000123",)
    assert persisted.historical_requirements == (requirement,)
