from campaigniq.access import AccessContext
from campaigniq.ui.oidc_access_gate import authorized_oidc_access


def test_authorized_oidc_access_returns_assigned_workspace() -> None:
    access = authorized_oidc_access(
        {
            "sub": "google-subject-dennis",
            "email": "dennis@example.com",
        },
        {
            "google-subject-dennis": "workspace-dennis",
            "google-subject-andrew": "workspace-andrew",
        },
    )

    assert access == AccessContext(
        identity_id="google-subject-dennis",
        workspace_id="workspace-dennis",
    )


def test_authenticated_but_unknown_identity_is_denied() -> None:
    access = authorized_oidc_access(
        {"sub": "google-subject-unknown"},
        {"google-subject-dennis": "workspace-dennis"},
    )

    assert access is None


def test_missing_subject_is_denied_even_when_email_matches() -> None:
    access = authorized_oidc_access(
        {"email": "dennis@example.com"},
        {"dennis@example.com": "workspace-dennis"},
    )

    assert access is None


def test_identity_cannot_select_another_workspace_through_claims() -> None:
    access = authorized_oidc_access(
        {
            "sub": "google-subject-dennis",
            "workspace_id": "workspace-andrew",
        },
        {
            "google-subject-dennis": "workspace-dennis",
            "google-subject-andrew": "workspace-andrew",
        },
    )

    assert access is not None
    assert access.workspace_id == "workspace-dennis"
