"""Application identity-to-workspace authorization boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class AccessContext:
    """Authorized CampaignIQ workspace for one authenticated identity."""

    identity_id: str
    workspace_id: str


def resolve_access_context(
    identity_id: str,
    workspace_by_identity: Mapping[str, str],
) -> AccessContext | None:
    """Resolve an authenticated identity to its authorized workspace.

    The workspace is selected only from trusted authorization data. Callers
    do not supply a requested workspace ID.
    """
    workspace_id = workspace_by_identity.get(identity_id)
    if workspace_id is None:
        return None

    return AccessContext(
        identity_id=identity_id,
        workspace_id=workspace_id,
    )
