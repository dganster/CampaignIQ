#!/usr/bin/env python3
"""Create Streamlit OIDC secrets from deployment environment variables."""

from __future__ import annotations

import json
import os
from pathlib import Path

AUTH_MODE_ENV = "CAMPAIGNIQ_AUTH_MODE"
OIDC_MODE = "oidc"

ENVIRONMENT = {
    "redirect_uri": "CAMPAIGNIQ_OIDC_REDIRECT_URI",
    "cookie_secret": "CAMPAIGNIQ_OIDC_COOKIE_SECRET",
    "client_id": "CAMPAIGNIQ_OIDC_CLIENT_ID",
    "client_secret": "CAMPAIGNIQ_OIDC_CLIENT_SECRET",
}

GOOGLE_METADATA_URL = (
    "https://accounts.google.com/.well-known/openid-configuration"
)


def _toml_string(value: str) -> str:
    """Encode a string safely as a TOML basic string."""
    return json.dumps(value)


def configure_streamlit_auth(
    *,
    environ: dict[str, str] | None = None,
    destination: Path | None = None,
) -> Path | None:
    env = os.environ if environ is None else environ

    if destination is None:
        destination = Path(".streamlit") / "secrets.toml"

    if env.get(AUTH_MODE_ENV, "").strip().lower() != OIDC_MODE:
        destination.unlink(missing_ok=True)
        return None

    values: dict[str, str] = {}
    missing: list[str] = []

    for setting, variable in ENVIRONMENT.items():
        value = env.get(variable, "").strip()
        if not value:
            missing.append(variable)
        else:
            values[setting] = value

    if missing:
        raise RuntimeError(
            "OIDC mode requires environment variables: "
            + ", ".join(sorted(missing))
        )

    destination.parent.mkdir(parents=True, exist_ok=True)

    content = (
        "[auth]\n"
        f"redirect_uri = {_toml_string(values['redirect_uri'])}\n"
        f"cookie_secret = {_toml_string(values['cookie_secret'])}\n"
        "\n"
        "[auth.google]\n"
        f"client_id = {_toml_string(values['client_id'])}\n"
        f"client_secret = {_toml_string(values['client_secret'])}\n"
        f"server_metadata_url = {_toml_string(GOOGLE_METADATA_URL)}\n"
    )

    destination.write_text(content, encoding="utf-8")
    destination.chmod(0o600)
    return destination


if __name__ == "__main__":
    configure_streamlit_auth()
