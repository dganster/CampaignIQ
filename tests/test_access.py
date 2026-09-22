from campaigniq.access import AccessContext, resolve_access_context


def test_resolve_access_context_maps_identity_to_authorized_workspace() -> None:
    access = resolve_access_context(
        "identity-dennis",
        {
            "identity-dennis": "workspace-dennis",
            "identity-andrew": "workspace-andrew",
        },
    )

    assert access == AccessContext(
        identity_id="identity-dennis",
        workspace_id="workspace-dennis",
    )


def test_resolve_access_context_keeps_identities_in_separate_workspaces() -> None:
    authorization = {
        "identity-dennis": "workspace-dennis",
        "identity-andrew": "workspace-andrew",
    }

    dennis = resolve_access_context("identity-dennis", authorization)
    andrew = resolve_access_context("identity-andrew", authorization)

    assert dennis is not None
    assert andrew is not None
    assert dennis.workspace_id == "workspace-dennis"
    assert andrew.workspace_id == "workspace-andrew"
    assert dennis.workspace_id != andrew.workspace_id


def test_resolve_access_context_denies_unknown_identity() -> None:
    access = resolve_access_context(
        "identity-unknown",
        {"identity-dennis": "workspace-dennis"},
    )

    assert access is None


def test_workspace_cannot_be_selected_through_resolver_arguments() -> None:
    authorization = {
        "identity-dennis": "workspace-dennis",
        "identity-andrew": "workspace-andrew",
    }

    access = resolve_access_context("identity-andrew", authorization)

    assert access is not None
    assert access.workspace_id == "workspace-andrew"
