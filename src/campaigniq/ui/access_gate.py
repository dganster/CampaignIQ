"""Temporary single-user access gate for the CampaignIQ web UI.

This is an interim cloud-deployment safeguard, not the eventual
multi-user identity and authorization system.
"""

from __future__ import annotations

import hmac
import os


CAMPAIGNIQ_ACCESS_PASSWORD_ENV = "CAMPAIGNIQ_ACCESS_PASSWORD"


def access_password() -> str | None:
    """Return the configured dashboard password, if any."""
    value = os.environ.get(CAMPAIGNIQ_ACCESS_PASSWORD_ENV)
    if value is None:
        return None

    value = value.strip()
    return value or None


def password_matches(candidate: str, expected: str) -> bool:
    """Compare passwords without ordinary string equality."""
    return hmac.compare_digest(candidate.encode(), expected.encode())
