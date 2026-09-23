from campaigniq.ui.access_gate import (
    AUTH_MODE_OIDC,
    AUTH_MODE_PASSWORD,
    access_password,
    authentication_mode,
    password_matches,
)


def test_access_password_is_none_when_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("CAMPAIGNIQ_ACCESS_PASSWORD", raising=False)

    assert access_password() is None


def test_access_password_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv("CAMPAIGNIQ_ACCESS_PASSWORD", "cloud-secret")

    assert access_password() == "cloud-secret"


def test_access_password_treats_blank_as_unconfigured(monkeypatch) -> None:
    monkeypatch.setenv("CAMPAIGNIQ_ACCESS_PASSWORD", "   ")

    assert access_password() is None


def test_password_matches_accepts_correct_password() -> None:
    assert password_matches("cloud-secret", "cloud-secret") is True


def test_password_matches_rejects_incorrect_password() -> None:
    assert password_matches("wrong", "cloud-secret") is False


def test_dashboard_requires_access_before_runtime_construction() -> None:
    from pathlib import Path

    dashboard = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "campaigniq"
        / "ui"
        / "dashboard.py"
    ).read_text()

    gate_position = dashboard.index("ACCESS = require_dashboard_access()")
    runtime_position = dashboard.index("RUNTIME = build_local_runtime(")

    assert gate_position < runtime_position
    assert (
        "workspace_id=ACCESS.workspace_id if ACCESS is not None else None"
        in dashboard
    )


def test_authentication_mode_defaults_to_password(monkeypatch) -> None:
    monkeypatch.delenv("CAMPAIGNIQ_AUTH_MODE", raising=False)

    assert authentication_mode() == AUTH_MODE_PASSWORD


def test_authentication_mode_accepts_oidc(monkeypatch) -> None:
    monkeypatch.setenv("CAMPAIGNIQ_AUTH_MODE", "oidc")

    assert authentication_mode() == AUTH_MODE_OIDC


def test_authentication_mode_normalizes_whitespace_and_case(monkeypatch) -> None:
    monkeypatch.setenv("CAMPAIGNIQ_AUTH_MODE", "  OIDC  ")

    assert authentication_mode() == AUTH_MODE_OIDC


def test_unknown_authentication_mode_falls_back_to_password(monkeypatch) -> None:
    monkeypatch.setenv("CAMPAIGNIQ_AUTH_MODE", "something-else")

    assert authentication_mode() == AUTH_MODE_PASSWORD
