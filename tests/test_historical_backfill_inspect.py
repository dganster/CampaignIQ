from datetime import date
from hashlib import sha256
import json
from uuid import uuid4

import pytest
import psycopg

import campaigniq.historical_backfill_inspect as module
from campaigniq.domain.lot_book import LotBook
from campaigniq.persistence.lot_book_store import serialize_lot_book

DSN = "dbname=campaigniq_dev host=localhost user=developer"
WORKSPACE = "workspace-dennis"


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self):
        self.calls = []
        self.identity = ("campaigniq_dev", "on", "repeatable read")
        self.artifacts = []
        self.drafts = []
        self.documents = []
        self.fail_select = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.startswith("BEGIN") or sql == "ROLLBACK":
            return Result([])
        if "current_database()" in sql:
            return Result([self.identity])
        if self.fail_select:
            raise RuntimeError("simulated query failure")
        if "artifact_storage_dev" in sql:
            return Result(self.artifacts)
        if "import_draft_documents" in sql:
            return Result(self.documents)
        if "import_drafts" in sql:
            return Result(self.drafts)
        raise AssertionError(sql)


@pytest.fixture
def database(monkeypatch):
    connection = Connection()
    def connect(**kwargs):
        assert kwargs["dbname"] == "campaigniq_dev"
        assert kwargs["hostaddr"] == "127.0.0.1"
        assert kwargs["options"] == "-c default_transaction_read_only=on"
        assert kwargs["autocommit"] is True
        return connection
    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    return connection


def load():
    return module.load_development_snapshot(DSN, WORKSPACE)


def book(end):
    return serialize_lot_book(period_end=end, lot_book=LotBook())


def add_draft(database, month=1, role=None, content=b"source", digest=None, revision=1):
    draft_id = uuid4()
    database.drafts.append((draft_id, WORKSPACE, 2025, month, 3))
    if role:
        database.documents.append((draft_id, role, "original.csv", "text/csv",
                                   content, digest or sha256(content).hexdigest(), revision))
    return draft_id


def test_one_verified_readonly_transaction_and_workspace_scoped_selects(database):
    load()
    assert database.calls[0][0] == "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert database.calls[-1][0] == "ROLLBACK"
    for sql, params in database.calls[2:-1]:
        assert sql.startswith("SELECT ")
        assert params == (WORKSPACE,)
    assert all("CREATE " not in sql and "UPDATE " not in sql for sql, _ in database.calls)


@pytest.mark.parametrize("identity", [
    ("production", "on", "repeatable read"),
    ("campaigniq_dev", "off", "repeatable read"),
    ("campaigniq_dev", "on", "read committed"),
])
def test_server_state_must_match_development_snapshot(database, identity):
    database.identity = identity
    with pytest.raises(ValueError, match="verification failed"):
        load()
    assert database.calls[-1][0] == "ROLLBACK"
    assert len(database.calls) == 3


def test_query_failure_rolls_back(database):
    database.fail_select = True
    with pytest.raises(RuntimeError):
        load()
    assert database.calls[-1][0] == "ROLLBACK"


@pytest.mark.parametrize("dsn", [
    "dbname=production host=localhost",
    "dbname=campaigniq_dev host=production.example",
    "dbname=campaigniq_dev host=localhost hostaddr=203.0.113.1",
    "dbname=campaigniq_dev host=localhost,production.example",
    "dbname=campaigniq_dev service=production",
    "host=localhost",
])
def test_remote_or_ambiguous_database_configuration_rejected(dsn):
    with pytest.raises(ValueError):
        module._connection_parameters(dsn)


def test_ambient_remote_host_is_overridden(monkeypatch):
    monkeypatch.setenv("PGHOST", "remote.example")
    monkeypatch.setenv("PGHOSTADDR", "203.0.113.1")
    parameters = module._connection_parameters("dbname=campaigniq_dev")
    assert parameters["host"] == "localhost"
    assert parameters["hostaddr"] == "127.0.0.1"


def test_ipv6_local_connection_supported():
    parameters = module._connection_parameters("dbname=campaigniq_dev host=::1")
    assert parameters["hostaddr"] == "::1"


def test_runner_refuses_render_before_connecting(database, monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    with pytest.raises(ValueError, match="Render"):
        load()
    assert not database.calls


def test_empty_development_database_reports_all_six_missing_drafts(database):
    report = module.inspect_snapshot(load())
    assert [row["month"] for row in report["months"]] == list(module.MONTHS)
    assert all(not row["draft_found"] and row["blocking_findings"] for row in report["months"])
    assert not report["publication_authorized"]
    assert not report["evidence_completeness_verified"]


def test_missing_inputs_use_real_preflight_and_do_not_invent_references(database):
    add_draft(database)
    report = module.inspect_snapshot(load())
    january = report["months"][0]
    assert not january["preflight_ready"]
    assert any("No authoritative opening" in v["message"] for v in january["validations"])
    assert any("No independent published closing" in b for b in january["blocking_findings"])
    assert "preview" not in january


@pytest.mark.parametrize("problem", ["checksum", "revision", "unsupported"])
def test_corrupt_or_unsupported_document_blocks_semantic_processing(database, problem):
    role = "unknown" if problem == "unsupported" else "thinkorswim_trade_history"
    add_draft(database, role=role,
              digest="bad" if problem == "checksum" else None,
              revision=4 if problem == "revision" else 1)
    row = module.inspect_snapshot(load())["months"][0]
    assert row["blocking_findings"]
    assert "preflight_ready" not in row
    assert "preview" not in row


def test_duplicate_active_month_rejected(database):
    add_draft(database)
    add_draft(database)
    with pytest.raises(ValueError, match="duplicate draft"):
        load()


def test_wrong_workspace_draft_rejected(database):
    database.drafts = [(uuid4(), "workspace-andrew", 2025, 1, 0)]
    with pytest.raises(ValueError, match="draft metadata"):
        load()


def test_document_outside_snapshot_rejected(database):
    database.documents = [(uuid4(), "role", "x", "text/plain", b"x", sha256(b"x").hexdigest(), 1)]
    with pytest.raises(ValueError, match="outside selected workspace"):
        load()


def test_duplicate_document_role_rejected(database):
    add_draft(database, role="thinkorswim_trade_history")
    database.documents.append(database.documents[0])
    with pytest.raises(ValueError, match="duplicate current document"):
        load()


def test_july_december_gap_and_published_2026_state_preserved(database):
    database.artifacts = [("2026-01-lot-book.json", book(date(2026, 1, 31)))]
    snapshot = load()
    before = snapshot.storage.read_text("2026-01-lot-book.json")
    report = module.inspect_snapshot(snapshot)
    assert report["continuity"]["missing_intervening_months"] == [
        f"2025-{m:02d}" for m in range(7, 13)]
    assert snapshot.storage.read_text("2026-01-lot-book.json") == before
    with pytest.raises(RuntimeError, match="write"):
        snapshot.storage.write_text("2026-01-lot-book.json", "changed")


def test_market_choices_limited_to_target_months(database):
    with pytest.raises(ValueError, match="January"):
        module.inspect_snapshot(load(), forex_not_applicable=["2026-01"])


def test_cli_requires_explicit_development_flag(monkeypatch):
    with pytest.raises(SystemExit):
        module.main(["--workspace", WORKSPACE])


def test_cli_does_not_expose_connection_secret(monkeypatch, capsys):
    monkeypatch.setenv("CAMPAIGNIQ_DEV_DATABASE_URL", "secret DSN")
    def fail(*args):
        raise RuntimeError("password=private-token")
    monkeypatch.setattr(module, "load_development_snapshot", fail)
    assert module.main(["--development-only", "--workspace", WORKSPACE]) == 1
    output = capsys.readouterr().out
    assert "private-token" not in output and "secret DSN" not in output
    assert not json.loads(output)["publication_authorized"]


def test_cli_outputs_valid_json_without_approval(database, monkeypatch, capsys):
    monkeypatch.setenv("CAMPAIGNIQ_DEV_DATABASE_URL", DSN)
    assert module.main(["--development-only", "--workspace", WORKSPACE]) == 0
    assert not json.loads(capsys.readouterr().out)["publication_authorized"]


def test_ready_draft_without_independent_reference_never_runs_preview(database, monkeypatch):
    from types import SimpleNamespace
    from campaigniq.import_contract import monthly_import_contract
    add_draft(database)
    monkeypatch.setattr(module, "prepare_monthly_import", lambda *a, **kw:
                        SimpleNamespace(ready=True, contract=monthly_import_contract(2025, 1),
                                        validations=()))
    def forbidden(*args, **kwargs):
        pytest.fail("missing reference reached preview")
    monkeypatch.setattr(module, "preview_saved_draft_for_replay", forbidden)
    row = module.inspect_snapshot(load())["months"][0]
    assert row["preflight_ready"]
    assert "preview" not in row
    assert row["blocking_findings"]


def test_published_reference_preview_discrepancies_remain_blocking(database, monkeypatch):
    from types import SimpleNamespace
    from campaigniq.import_contract import monthly_import_contract
    add_draft(database)
    reference = book(date(2025, 1, 31))
    database.artifacts = [("2025-01-lot-book.json", reference)]
    monkeypatch.setattr(module, "prepare_monthly_import", lambda *a, **kw:
                        SimpleNamespace(ready=True, contract=monthly_import_contract(2025, 1),
                                        validations=()))
    def preview(repo, draft_id, storage, **kwargs):
        assert kwargs["reference_closing_text"] == reference
        return SimpleNamespace(report={"blocking_findings": ["META discrepancy"],
                                       "publication_authorized": False})
    monkeypatch.setattr(module, "preview_saved_draft_for_replay", preview)
    row = module.inspect_snapshot(load())["months"][0]
    assert "META discrepancy" in row["blocking_findings"]
    assert not row["preview"]["publication_authorized"]
