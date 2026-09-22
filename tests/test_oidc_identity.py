from campaigniq.access import AccessContext
from campaigniq.oidc_identity import (
    authenticated_identity_id,
    resolve_oidc_access_context,
)


def test_authenticated_identity_id_uses_oidc_subject() -> None:
    claims = {
        "sub": "google-subject-123",
        "email": "dennis@example.com",
        "name": "Dennis",
    }

    assert authenticated_identity_id(claims) == "google-subject-123"


def test_authenticated_identity_id_does_not_fall_back_to_email() -> None:
    claims = {
        "email": "dennis@example.com",
        "name": "Dennis",
    }

    assert authenticated_identity_id(claims) is None


def test_authenticated_identity_id_rejects_blank_subject() -> None:
    assert authenticated_identity_id({"sub": "   "}) is None


def test_authenticated_identity_id_rejects_non_string_subject() -> None:
    assert authenticated_identity_id({"sub": 12345}) is None


def test_resolve_oidc_access_context_maps_subject_to_workspace() -> None:
    access = resolve_oidc_access_context(
        {
            "sub": "google-subject-123",
            "email": "dennis@example.com",
        },
        {
            "google-subject-123": "workspace-dennis",
            "google-subject-456": "workspace-andrew",
        },
    )

    assert access == AccessContext(
        identity_id="google-subject-123",
        workspace_id="workspace-dennis",
    )


def test_resolve_oidc_access_context_denies_unknown_subject() -> None:
    access = resolve_oidc_access_context(
        {"sub": "google-subject-unknown"},
        {"google-subject-123": "workspace-dennis"},
    )

    assert access is None


def test_resolve_oidc_access_context_denies_missing_subject() -> None:
    access = resolve_oidc_access_context(
        {"email": "dennis@example.com"},
        {"google-subject-123": "workspace-dennis"},
    )

    assert access is None


def test_email_cannot_select_another_workspace() -> None:
    access = resolve_oidc_access_context(
        {
            "sub": "google-subject-456",
            "email": "dennis@example.com",
        },
        {
            "google-subject-123": "workspace-dennis",
            "google-subject-456": "workspace-andrew",
        },
    )

    assert access == AccessContext(
        identity_id="google-subject-456",
        workspace_id="workspace-andrew",
    )
