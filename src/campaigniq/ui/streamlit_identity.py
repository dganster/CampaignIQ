"""Streamlit adapter for authenticated OIDC identity claims."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def oidc_claims_from_streamlit_user(
    user: Any,
) -> Mapping[str, Any]:
    """Return authenticated OIDC claims exposed by Streamlit.

    Streamlit's user object exposes ``to_dict()`` after authentication.
    Returning an empty mapping keeps missing or unusable identity data
    fail-closed at the provider-neutral OIDC boundary.
    """
    to_dict = getattr(user, "to_dict", None)
    if not callable(to_dict):
        return {}

    claims = to_dict()
    if not isinstance(claims, Mapping):
        return {}

    return claims
