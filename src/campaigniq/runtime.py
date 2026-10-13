"""Runtime composition for CampaignIQ application infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from campaigniq.persistence.artifact_storage import (
    ArtifactStorage,
    LocalFilesystemArtifactStorage,
)


CAMPAIGNIQ_DATA_ROOT_ENV = "CAMPAIGNIQ_DATA_ROOT"


@dataclass(frozen=True)
class CampaignIQRuntime:
    """Infrastructure dependencies required by the CampaignIQ application."""

    artifact_storage: ArtifactStorage
    authoritative_state_root: Path
    historical_source_root: Path


def build_local_runtime(
    *,
    project_root: str | Path | None = None,
    workspace_id: str | None = None,
) -> CampaignIQRuntime:
    """Build a filesystem-backed CampaignIQ runtime.

    CAMPAIGNIQ_DATA_ROOT can place runtime state on durable infrastructure
    such as a mounted cloud disk. When it is unset, CampaignIQ preserves the
    existing local behavior of storing runtime state under
    <project_root>/.campaigniq.
    """
    configured_data_root = os.environ.get(CAMPAIGNIQ_DATA_ROOT_ENV)

    if configured_data_root:
        runtime_data_root = Path(configured_data_root).expanduser()
    else:
        root = (
            Path(project_root)
            if project_root is not None
            else Path(__file__).resolve().parents[2]
        )
        runtime_data_root = root / ".campaigniq"

    if workspace_id is not None:
        workspace_path = Path(workspace_id)
        if (
            not workspace_id
            or workspace_path.is_absolute()
            or len(workspace_path.parts) != 1
            or workspace_id in {".", ".."}
        ):
            raise ValueError(f"Invalid workspace ID: {workspace_id!r}")

        runtime_data_root = runtime_data_root / "workspaces" / workspace_id

    authoritative_state_root = runtime_data_root / "authoritative_state"
    historical_source_root = runtime_data_root / "thinkorswim_history"

    authoritative_state_root.mkdir(parents=True, exist_ok=True)
    historical_source_root.mkdir(parents=True, exist_ok=True)

    backend = os.environ.get("CAMPAIGNIQ_STORAGE_BACKEND", "filesystem").strip().lower()
    if backend == "filesystem":
        artifact_storage = LocalFilesystemArtifactStorage(authoritative_state_root)
    elif backend == "postgres":
        dsn = os.environ.get("CAMPAIGNIQ_DEV_DATABASE_URL")
        if not dsn:
            raise ValueError(
                "CAMPAIGNIQ_DEV_DATABASE_URL is required when "
                "CAMPAIGNIQ_STORAGE_BACKEND=postgres"
            )
        from campaigniq.persistence.postgres_artifact_storage import PostgresArtifactStorage
        artifact_storage = PostgresArtifactStorage(
            dsn, namespace=workspace_id if workspace_id is not None else "shared"
        )
    else:
        raise ValueError(f"Unsupported CAMPAIGNIQ_STORAGE_BACKEND: {backend!r}")

    return CampaignIQRuntime(
        artifact_storage=artifact_storage,
        authoritative_state_root=authoritative_state_root,
        historical_source_root=historical_source_root,
    )
