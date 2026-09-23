"""Temporary single-user access gate for the CampaignIQ web UI.

This is an interim cloud-deployment safeguard, not the eventual
multi-user identity and authorization system.
"""

from __future__ import annotations

import hmac
import os


CAMPAIGNIQ_ACCESS_PASSWORD_ENV = "CAMPAIGNIQ_ACCESS_PASSWORD"
CAMPAIGNIQ_AUTH_MODE_ENV = "CAMPAIGNIQ_AUTH_MODE"

AUTH_MODE_PASSWORD = "password"
AUTH_MODE_OIDC = "oidc"


def authentication_mode() -> str:
    """Return the configured dashboard authentication mode.

    Password mode remains the default so deploying OIDC-capable code does
    not activate OIDC until server configuration explicitly selects it.
    """
    value = os.environ.get(CAMPAIGNIQ_AUTH_MODE_ENV)
    if value is None or not value.strip():
        return AUTH_MODE_PASSWORD

    value = value.strip().lower()
    if value not in {AUTH_MODE_PASSWORD, AUTH_MODE_OIDC}:
        return AUTH_MODE_PASSWORD

    return value


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
