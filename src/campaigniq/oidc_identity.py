"""Provider-neutral adapter for authenticated OIDC identity claims."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from campaigniq.access import AccessContext, resolve_access_context


def authenticated_identity_id(
    claims: Mapping[str, Any],
) -> str | None:
    """Return the stable OIDC subject identifier from authenticated claims."""
    subject = claims.get("sub")

    if not isinstance(subject, str):
        return None

    subject = subject.strip()
    return subject or None


def resolve_oidc_access_context(
    claims: Mapping[str, Any],
    workspace_by_identity: Mapping[str, str],
) -> AccessContext | None:
    """Resolve authenticated OIDC claims to an authorized CampaignIQ workspace."""
    identity_id = authenticated_identity_id(claims)
    if identity_id is None:
        return None

    return resolve_access_context(
        identity_id,
        workspace_by_identity,
    )
