from pathlib import Path

from campaigniq.runtime import build_local_runtime
from campaigniq.ui.oidc_access_gate import authorized_oidc_access


def test_authorized_identities_receive_isolated_workspace_storage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Authorization-selected workspaces remain isolated through runtime storage."""
    data_root = tmp_path / "mounted-cloud-storage"
    monkeypatch.setenv("CAMPAIGNIQ_DATA_ROOT", str(data_root))

    authorization = {
        "google-subject-dennis": "workspace-dennis",
        "google-subject-andrew": "workspace-andrew",
    }

    # Browser claims cannot choose the workspace. Both users attempt to claim
    # the other workspace; trusted server-side authorization must win.
    dennis_access = authorized_oidc_access(
        {
            "sub": "google-subject-dennis",
            "workspace_id": "workspace-andrew",
        },
        authorization,
    )
    andrew_access = authorized_oidc_access(
        {
            "sub": "google-subject-andrew",
            "workspace_id": "workspace-dennis",
        },
        authorization,
    )

    assert dennis_access is not None
    assert andrew_access is not None
    assert dennis_access.workspace_id == "workspace-dennis"
    assert andrew_access.workspace_id == "workspace-andrew"

    dennis_runtime = build_local_runtime(
        workspace_id=dennis_access.workspace_id,
    )
    andrew_runtime = build_local_runtime(
        workspace_id=andrew_access.workspace_id,
    )

    artifact = "2026-08-realized-attributions.json"

    dennis_runtime.artifact_storage.write_text(
        artifact,
        "dennis-private-data",
    )
    andrew_runtime.artifact_storage.write_text(
        artifact,
        "andrew-private-data",
    )

    assert dennis_runtime.artifact_storage.read_text(artifact) == (
        "dennis-private-data"
    )
    assert andrew_runtime.artifact_storage.read_text(artifact) == (
        "andrew-private-data"
    )

    assert dennis_runtime.authoritative_state_root == (
        data_root
        / "workspaces"
        / "workspace-dennis"
        / "authoritative_state"
    )
    assert andrew_runtime.authoritative_state_root == (
        data_root
        / "workspaces"
        / "workspace-andrew"
        / "authoritative_state"
    )

    assert dennis_runtime.authoritative_state_root != (
        andrew_runtime.authoritative_state_root
    )
    assert dennis_runtime.historical_source_root != (
        andrew_runtime.historical_source_root
    )


def test_unknown_identity_never_reaches_runtime_storage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """An identity without server authorization receives no workspace."""
    monkeypatch.setenv(
        "CAMPAIGNIQ_DATA_ROOT",
        str(tmp_path / "mounted-cloud-storage"),
    )

    access = authorized_oidc_access(
        {
            "sub": "google-subject-unknown",
            "workspace_id": "workspace-dennis",
        },
        {
            "google-subject-dennis": "workspace-dennis",
            "google-subject-andrew": "workspace-andrew",
        },
    )

    assert access is None
