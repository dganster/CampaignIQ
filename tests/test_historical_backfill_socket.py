from unittest.mock import patch

import pytest

from campaigniq.historical_backfill_inspect import (
    _connection_parameters, load_development_snapshot,
)


@pytest.fixture(autouse=True)
def clean_routing(monkeypatch):
    for name in ("PGHOSTADDR", "PGSERVICE", "RENDER", "RENDER_SERVICE_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("directory", ["/var/run/postgresql", "/run/postgresql", "/tmp"])
def test_standard_local_socket_preserves_readonly_options(directory):
    parameters = _connection_parameters(f"dbname=campaigniq_dev user=dganster host={directory}")
    assert parameters["host"] == directory
    assert parameters["hostaddr"] == ""
    assert parameters["user"] == "dganster"
    assert parameters["options"] == "-c default_transaction_read_only=on"
    assert parameters["connect_timeout"] == "10"


@pytest.mark.parametrize("directory", ["/custom/socket", "@abstract", "/tmp,/remote", "/tmp/../remote"])
def test_unapproved_or_multiple_socket_locations_rejected(directory):
    with pytest.raises(ValueError):
        _connection_parameters(f"dbname=campaigniq_dev host={directory}")


@pytest.mark.parametrize("address", ["127.0.0.1", "203.0.113.1"])
def test_socket_with_explicit_hostaddr_rejected(address):
    with pytest.raises(ValueError, match="hostaddr"):
        _connection_parameters(f"dbname=campaigniq_dev host=/var/run/postgresql hostaddr={address}")


@pytest.mark.parametrize("variable,value", [
    ("PGHOSTADDR", "203.0.113.1"), ("PGSERVICE", "remote-service"),
])
def test_socket_ambient_routing_rejected_without_modifying_environment(monkeypatch, variable, value):
    import os
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError):
        _connection_parameters("dbname=campaigniq_dev host=/var/run/postgresql")
    assert os.environ[variable] == value


def test_socket_still_requires_development_database():
    with pytest.raises(ValueError, match="campaigniq_dev"):
        _connection_parameters("dbname=production host=/var/run/postgresql")


def test_socket_still_rejects_explicit_service():
    with pytest.raises(ValueError):
        _connection_parameters("dbname=campaigniq_dev host=/var/run/postgresql service=remote")


def test_socket_loader_uses_existing_readonly_transaction_and_rollback():
    calls = []
    class Rows:
        def __init__(self, values):
            self.values = values
        def fetchone(self):
            return self.values[0]
        def fetchall(self):
            return self.values
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, sql, params=None):
            calls.append((sql, params))
            if "current_database()" in sql:
                return Rows([("campaigniq_dev", "on", "repeatable read")])
            return Rows([])
    def connect(**kwargs):
        assert kwargs["host"] == "/var/run/postgresql"
        assert kwargs["hostaddr"] == ""
        assert kwargs["autocommit"] is True
        assert kwargs["options"] == "-c default_transaction_read_only=on"
        return Connection()
    with patch("psycopg.connect", side_effect=connect):
        snapshot = load_development_snapshot(
            "dbname=campaigniq_dev user=dganster host=/var/run/postgresql",
            "workspace-dennis")
    assert not snapshot.repository.list_drafts()
    assert calls[0][0] == "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert calls[-1][0] == "ROLLBACK"
    assert all(sql.startswith("SELECT") for sql, _ in calls[1:-1])


def test_socket_still_refuses_render(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    with patch("psycopg.connect") as connect:
        with pytest.raises(ValueError, match="Render"):
            load_development_snapshot(
                "dbname=campaigniq_dev host=/var/run/postgresql", "workspace-dennis")
        connect.assert_not_called()
