from campaigniq.ui.workspace_authorization import workspace_authorization


def test_workspace_authorization_reads_server_configuration(monkeypatch) -> None:
    monkeypatch.setenv(
        "CAMPAIGNIQ_WORKSPACE_AUTHORIZATION",
        '{"identity-dennis":"workspace-dennis",'
        '"identity-andrew":"workspace-andrew"}',
    )

    assert workspace_authorization() == {
        "identity-dennis": "workspace-dennis",
        "identity-andrew": "workspace-andrew",
    }


def test_workspace_authorization_is_empty_when_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("CAMPAIGNIQ_WORKSPACE_AUTHORIZATION", raising=False)

    assert workspace_authorization() == {}


def test_workspace_authorization_rejects_malformed_json(monkeypatch) -> None:
    monkeypatch.setenv(
        "CAMPAIGNIQ_WORKSPACE_AUTHORIZATION",
        "not-json",
    )

    assert workspace_authorization() == {}


def test_workspace_authorization_rejects_non_object_configuration(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "CAMPAIGNIQ_WORKSPACE_AUTHORIZATION",
        '["workspace-dennis"]',
    )

    assert workspace_authorization() == {}


def test_workspace_authorization_rejects_non_string_entries(monkeypatch) -> None:
    monkeypatch.setenv(
        "CAMPAIGNIQ_WORKSPACE_AUTHORIZATION",
        '{"identity-dennis":123}',
    )

    assert workspace_authorization() == {}


def test_workspace_authorization_rejects_blank_entries(monkeypatch) -> None:
    monkeypatch.setenv(
        "CAMPAIGNIQ_WORKSPACE_AUTHORIZATION",
        '{"identity-dennis":"   "}',
    )

    assert workspace_authorization() == {}
