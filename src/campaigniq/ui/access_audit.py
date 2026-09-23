"""Security-relevant audit events for CampaignIQ web access."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from campaigniq.oidc_identity import authenticated_identity_id


LOGGER = logging.getLogger(__name__)


def audit_unauthorized_oidc_identity(
    claims: Mapping[str, Any],
) -> None:
    """Log the stable subject of an authenticated but unauthorized identity.

    Only the provider-neutral OIDC subject is recorded. Other claims,
    credentials, and tokens are deliberately excluded.
    """
    identity_id = authenticated_identity_id(claims)
    if identity_id is None:
        return

    LOGGER.warning(
        "CampaignIQ authenticated identity is not authorized: identity_id=%s",
        identity_id,
    )
