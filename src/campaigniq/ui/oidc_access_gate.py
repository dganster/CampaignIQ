"""OIDC authorization boundary for the CampaignIQ web UI."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from campaigniq.access import AccessContext
from campaigniq.oidc_identity import resolve_oidc_access_context
from campaigniq.ui.streamlit_identity import oidc_claims_from_streamlit_user


def authorized_oidc_access(
    claims: Mapping[str, Any],
    workspace_by_identity: Mapping[str, str],
) -> AccessContext | None:
    """Return authorized workspace access for authenticated OIDC claims.

    Authentication claims alone never grant CampaignIQ access. The stable
    OIDC subject must also be present in trusted server-side authorization
    data.
    """
    return resolve_oidc_access_context(
        claims,
        workspace_by_identity,
    )


def authorized_streamlit_access(
    user: Any,
    workspace_by_identity: Mapping[str, str],
) -> AccessContext | None:
    """Resolve a Streamlit OIDC user to authorized CampaignIQ access."""
    claims = oidc_claims_from_streamlit_user(user)
    return authorized_oidc_access(
        claims,
        workspace_by_identity,
    )
