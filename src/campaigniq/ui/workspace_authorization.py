"""Server-side workspace authorization configuration."""

from __future__ import annotations

import json
import os

CAMPAIGNIQ_WORKSPACE_AUTHORIZATION_ENV = "CAMPAIGNIQ_WORKSPACE_AUTHORIZATION"


def workspace_authorization() -> dict[str, str]:
    """Return the configured identity-to-workspace authorization mapping.

    Invalid, missing, or malformed configuration fails closed by returning
    an empty mapping.
    """
    value = os.environ.get(CAMPAIGNIQ_WORKSPACE_AUTHORIZATION_ENV)
    if value is None or not value.strip():
        return {}

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}

    if not isinstance(parsed, dict):
        return {}

    authorization: dict[str, str] = {}

    for identity_id, workspace_id in parsed.items():
        if not isinstance(identity_id, str) or not isinstance(workspace_id, str):
            return {}

        identity_id = identity_id.strip()
        workspace_id = workspace_id.strip()

        if not identity_id or not workspace_id:
            return {}

        authorization[identity_id] = workspace_id

    return authorization
