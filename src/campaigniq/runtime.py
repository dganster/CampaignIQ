"""Runtime composition for CampaignIQ application infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from campaigniq.persistence.artifact_storage import (
    ArtifactStorage,
    LocalFilesystemArtifactStorage,
)


@dataclass(frozen=True)
class CampaignIQRuntime:
    """Infrastructure dependencies required by the CampaignIQ application."""

    artifact_storage: ArtifactStorage
    authoritative_state_root: Path
    historical_source_root: Path


def build_local_runtime(*, project_root: str | Path | None = None) -> CampaignIQRuntime:
    """Build the current local-filesystem runtime.

    Keeping construction here gives the UI one composition boundary. A future
    cloud runtime can provide durable storage without changing analytics or
    import behavior.
    """
    root = (
        Path(project_root)
        if project_root is not None
        else Path(__file__).resolve().parents[2]
    )
    runtime_data_root = root / ".campaigniq"
    authoritative_state_root = runtime_data_root / "authoritative_state"
    historical_source_root = runtime_data_root / "thinkorswim_history"

    authoritative_state_root.mkdir(parents=True, exist_ok=True)
    historical_source_root.mkdir(parents=True, exist_ok=True)

    return CampaignIQRuntime(
        artifact_storage=LocalFilesystemArtifactStorage(authoritative_state_root),
        authoritative_state_root=authoritative_state_root,
        historical_source_root=historical_source_root,
    )
