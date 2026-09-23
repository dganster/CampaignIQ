from pathlib import Path

import pytest

from scripts.configure_streamlit_auth import configure_streamlit_auth


def test_password_mode_does_not_create_secrets(tmp_path: Path) -> None:
    destination = tmp_path / "secrets.toml"

    result = configure_streamlit_auth(
        environ={"CAMPAIGNIQ_AUTH_MODE": "password"},
        destination=destination,
    )

    assert result is None
    assert not destination.exists()


def test_password_mode_removes_stale_secrets(tmp_path: Path) -> None:
    destination = tmp_path / "secrets.toml"
    destination.write_text("stale-secret")

    result = configure_streamlit_auth(
        environ={"CAMPAIGNIQ_AUTH_MODE": "password"},
        destination=destination,
    )

    assert result is None
    assert not destination.exists()


def test_oidc_mode_requires_complete_configuration(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="CAMPAIGNIQ_OIDC_CLIENT_SECRET"):
        configure_streamlit_auth(
            environ={
                "CAMPAIGNIQ_AUTH_MODE": "oidc",
                "CAMPAIGNIQ_OIDC_REDIRECT_URI": "https://example.test/oauth2callback",
                "CAMPAIGNIQ_OIDC_COOKIE_SECRET": "cookie-secret",
                "CAMPAIGNIQ_OIDC_CLIENT_ID": "client-id",
            },
            destination=tmp_path / "secrets.toml",
        )


def test_oidc_mode_writes_streamlit_google_configuration(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "secrets.toml"

    result = configure_streamlit_auth(
        environ={
            "CAMPAIGNIQ_AUTH_MODE": "oidc",
            "CAMPAIGNIQ_OIDC_REDIRECT_URI": "https://example.test/oauth2callback",
            "CAMPAIGNIQ_OIDC_COOKIE_SECRET": 'cookie-"secret"',
            "CAMPAIGNIQ_OIDC_CLIENT_ID": "client-id",
            "CAMPAIGNIQ_OIDC_CLIENT_SECRET": "client-secret",
        },
        destination=destination,
    )

    assert result == destination
    content = destination.read_text(encoding="utf-8")

    assert "[auth]" in content
    assert 'redirect_uri = "https://example.test/oauth2callback"' in content
    assert 'cookie_secret = "cookie-\\\"secret\\\""' in content
    assert "[auth.google]" in content
    assert 'client_id = "client-id"' in content
    assert 'client_secret = "client-secret"' in content
    assert (
        'server_metadata_url = '
        '"https://accounts.google.com/.well-known/openid-configuration"'
    ) in content

    assert destination.stat().st_mode & 0o777 == 0o600
