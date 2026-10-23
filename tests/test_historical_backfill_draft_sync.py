import base64
import copy
from datetime import datetime
from hashlib import sha256
import json
from uuid import uuid4

import pytest
import psycopg

import campaigniq.historical_backfill_draft_sync as module

DSN = "dbname=campaigniq_dev user=dganster host=/var/run/postgresql"
STAMP = "2026-10-22T12:00:00+00:00"


def bundle(months=("2025-05", "2025-06")):
    result = {"format": module.FORMAT, "version": 1, "workspace": module.WORKSPACE,
              "months": list(months), "drafts": [], "documents": []}
    for month in months:
        draft_id = str(uuid4())
        result["drafts"].append(dict(id=draft_id, workspace=module.WORKSPACE,
            year=2025, month=int(month[-2:]), state="draft", revision=2,
            created_at=STAMP, updated_at=STAMP))
        for revision in (1, 2):
            content = f"original PDF bytes {month} revision {revision}".encode()
            result["documents"].append(dict(
                id=str(uuid4()), draft_id=draft_id, role="schwab_brokerage_statement",
                filename="Brokerage Statement.pdf", media_type="application/pdf",
                sha256=sha256(content).hexdigest(), content_base64=base64.b64encode(content).decode(),
                byte_size=len(content), revision=revision, uploaded_at=STAMP,
                superseded=revision == 1))
    return result


def empty(source):
    return dict(source, drafts=[], documents=[])


class Rows:
    def __init__(self, rows): self.rows = rows
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows


class Database:
    def __init__(self, source):
        self.bundle = copy.deepcopy(source)
        self.calls = []
        self.readonly = True
        self.saved = None
        self.failure = None
        self.dbname = "campaigniq_dev"
        self.collision = False

    def __enter__(self): return self
    def __exit__(self, *args): pass

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.startswith("BEGIN"):
            self.saved = copy.deepcopy(self.bundle)
            self.readonly = "READ ONLY" in sql
        elif sql == "ROLLBACK":
            self.bundle = self.saved
        elif sql == "COMMIT":
            if self.failure == "commit": raise RuntimeError("commit failed")
            self.saved = copy.deepcopy(self.bundle)
        elif sql.startswith("LOCK"):
            assert not self.readonly
        elif "current_database()" in sql:
            return Rows([(self.dbname, "on" if self.readonly else "off",
                          "repeatable read" if self.readonly else "serializable")])
        elif sql.startswith("SELECT id FROM"):
            if self.collision: return Rows([(uuid4(),)])
            rows = self.bundle["documents"] if "import_draft_documents" in sql else self.bundle["drafts"]
            wanted = params[0] if isinstance(params[0], list) else [params[0]]
            return Rows([(r["id"],) for r in rows if r["id"] in {str(i) for i in wanted}])
        elif sql.startswith("SELECT") and "JOIN" not in sql:
            assert params[0] == module.WORKSPACE
            return Rows([tuple(datetime.fromisoformat(r[f]) if f in {"created_at", "updated_at"}
                               else r[f] for f in module.DRAFT_FIELDS.split(","))
                         for r in self.bundle["drafts"]])
        elif sql.startswith("SELECT") and "JOIN" in sql:
            assert params[0] == module.WORKSPACE
            return Rows([tuple(
                base64.b64decode(r["content_base64"]) if f == "content"
                else datetime.fromisoformat(r[f]) if f == "uploaded_at" else r[f]
                for f in module.DOCUMENT_FIELDS.split(","))
                for r in self.bundle["documents"]])
        elif sql.startswith("INSERT INTO public.import_drafts "):
            assert not self.readonly
            self.bundle["drafts"].append(dict(zip(module.DRAFT_FIELDS.split(","), params)))
        elif sql.startswith("INSERT INTO public.import_draft_documents "):
            assert not self.readonly
            if self.failure == "document": raise RuntimeError("document insert failed")
            row = dict(zip(module.DOCUMENT_FIELDS.split(","), params))
            raw = row.pop("content")
            row.update(content_base64=base64.b64encode(raw).decode(), byte_size=len(raw))
            self.bundle["documents"].append(row)
        elif sql.startswith("DELETE FROM public.import_draft_documents"):
            self.bundle["documents"] = [r for r in self.bundle["documents"] if r["draft_id"] != str(params[0])]
        elif sql.startswith("DELETE FROM public.import_drafts"):
            self.bundle["drafts"] = [r for r in self.bundle["drafts"] if r["id"] != str(params[0])]
        else: raise AssertionError(sql)
        return Rows([])


@pytest.fixture
def setup(monkeypatch):
    for key in ("RENDER", "RENDER_SERVICE_ID", "PGHOSTADDR", "PGSERVICE"):
        monkeypatch.delenv(key, raising=False)
    source = bundle()
    database = Database(empty(source))
    def connect(*args, **kwargs):
        assert kwargs["autocommit"] is True
        assert "default_transaction_read_only=" in kwargs["options"]
        if not args:
            assert kwargs["dbname"] == "campaigniq_dev"
            assert kwargs["host"] == "/var/run/postgresql"
        return database
    monkeypatch.setattr(psycopg, "connect", connect)
    return source, database


def apply(source, tmp_path):
    return module.sync_drafts(DSN, source, apply=True,
                             backup=tmp_path/"before.json", receipt=tmp_path/"receipt.json")


def test_bundle_roundtrip_preserves_original_bytes_and_history(tmp_path):
    source = bundle()
    digest = module.write_bundle(tmp_path/"drafts.json", source)
    restored = module.read_bundle(tmp_path/"drafts.json", digest)
    assert restored == source
    assert len(restored["documents"]) == 4
    assert sum(r["superseded"] for r in restored["documents"]) == 2
    assert (tmp_path/"drafts.json").stat().st_mode & 0o777 == 0o600


def test_existing_export_is_not_overwritten(tmp_path):
    path = tmp_path/"drafts.json"
    module.write_bundle(path, bundle())
    original = path.read_bytes()
    with pytest.raises(FileExistsError): module.write_bundle(path, bundle())
    assert path.read_bytes() == original


@pytest.mark.parametrize("mutation", [
    lambda b: b.update(workspace="workspace-andrew"),
    lambda b: b["drafts"][0].update(month=9),
    lambda b: b["documents"][0].update(sha256="bad"),
    lambda b: b["documents"][0].update(revision=3),
    lambda b: b["documents"][0].update(content_base64="not base64"),
    lambda b: b["documents"][0].update(draft_id=str(uuid4())),
    lambda b: b["documents"].append(copy.deepcopy(b["documents"][0])),
    lambda b: b["documents"][0].update(superseded=False),
    lambda b: b["drafts"][0].update(revision=True),
    lambda b: b["documents"][0].update(byte_size=0),
    lambda b: b["documents"][0].update(uploaded_at="2026-10-22T12:00:00"),
])
def test_corrupt_or_out_of_scope_bundle_rejected(mutation):
    source = bundle()
    mutation(source)
    with pytest.raises((ValueError, TypeError)): module.validate_bundle(source)


def test_wrong_bundle_file_checksum_rejected(tmp_path):
    module.write_bundle(tmp_path/"bundle.json", bundle())
    with pytest.raises(ValueError, match="file checksum"):
        module.read_bundle(tmp_path/"bundle.json", "bad")


def test_export_is_readonly_and_rolls_back_source(setup, tmp_path):
    source, db = setup
    db.bundle = copy.deepcopy(source)
    db.dbname = "live_account_database"
    original = copy.deepcopy(db.bundle)
    result = module.export_drafts("source connection", source["months"], tmp_path/"export.json")
    assert db.bundle == original
    assert db.calls[0][0] == "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert db.calls[-1][0] == "ROLLBACK"
    assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE", "CREATE")) for sql, _ in db.calls)
    assert result["source_read_only"]
    assert module.read_bundle(tmp_path/"export.json", result["sha256"]) == source


def test_export_missing_selected_month_refuses_partial_bundle(setup, tmp_path):
    source, db = setup
    with pytest.raises(ValueError, match="not found"):
        module.export_drafts("source", source["months"], tmp_path/"export.json")
    assert not (tmp_path/"export.json").exists()
    assert db.calls[-1][0] == "ROLLBACK"


def test_import_default_is_readonly_plan(setup, tmp_path):
    source, db = setup
    result = module.sync_drafts(DSN, source)
    assert not result["applied"] and len(result["insert_draft_ids"]) == 2
    assert not db.bundle["drafts"]
    assert db.calls[-1][0] == "ROLLBACK"
    assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE", "LOCK")) for sql, _ in db.calls)


def test_apply_backs_up_then_preserves_ids_revisions_and_all_original_documents(setup, tmp_path):
    source, db = setup
    result = apply(source, tmp_path)
    assert result["applied"]
    assert db.bundle == source
    assert module.read_bundle(tmp_path/"before.json", result["backup_sha256"]) == empty(source)
    assert sha256((tmp_path/"receipt.json").read_bytes()).hexdigest() == result["receipt_sha256"]
    assert db.calls[-1][0] == "COMMIT"


def test_identical_drafts_are_skipped(setup, tmp_path):
    source, db = setup
    db.bundle = copy.deepcopy(source)
    result = apply(source, tmp_path)
    assert result["insert_draft_ids"] == []
    assert result["identical_months"] == source["months"]
    assert not any(sql.startswith("INSERT") for sql, _ in db.calls)


def test_differing_local_drafts_refuse_all_writes(setup, tmp_path):
    source, db = setup
    db.bundle = copy.deepcopy(source)
    db.bundle["drafts"][0]["revision"] = 3
    original = copy.deepcopy(db.bundle)
    with pytest.raises(ValueError, match="differ"):
        apply(source, tmp_path)
    assert db.bundle == original
    assert not (tmp_path/"before.json").exists()
    assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql, _ in db.calls)


@pytest.mark.parametrize("failure", ["document", "commit"])
def test_insert_or_commit_failure_rolls_back_every_added_row(setup, tmp_path, failure):
    source, db = setup
    db.failure = failure
    with pytest.raises(RuntimeError): apply(source, tmp_path)
    assert db.bundle == empty(source)
    assert db.calls[-1][0] == "ROLLBACK"
    assert (tmp_path/"before.json").exists()


def test_backup_path_collision_prevents_database_writes(setup, tmp_path):
    source, db = setup
    (tmp_path/"before.json").write_text("keep")
    with pytest.raises(FileExistsError): apply(source, tmp_path)
    assert (tmp_path/"before.json").read_text() == "keep"
    assert not any(sql.startswith("INSERT") for sql, _ in db.calls)


def test_receipt_path_collision_prevents_database_writes(setup, tmp_path):
    source, db = setup
    (tmp_path/"receipt.json").write_text("keep")
    with pytest.raises(FileExistsError): apply(source, tmp_path)
    assert not any(sql.startswith("INSERT") for sql, _ in db.calls)


def test_existing_uuid_collision_refuses_apply(setup, tmp_path):
    source, db = setup
    db.collision = True
    with pytest.raises(ValueError, match="UUID"):
        apply(source, tmp_path)
    assert not any(sql.startswith("INSERT") for sql, _ in db.calls)


def test_undo_removes_only_receipt_listed_new_drafts(setup, tmp_path):
    source, db = setup
    # Existing May draft is identical and must survive undo.
    db.bundle = dict(source, drafts=copy.deepcopy(source["drafts"][:1]),
                     documents=copy.deepcopy(source["documents"][:2]))
    result = apply(source, tmp_path)
    undo = module.undo_sync(DSN, source, tmp_path/"receipt.json", result["receipt_sha256"])
    assert undo["removed_imported_drafts"] == 1
    assert db.bundle["drafts"] == source["drafts"][:1]
    assert db.bundle["documents"] == source["documents"][:2]


def test_undo_refuses_changed_imported_draft(setup, tmp_path):
    source, db = setup
    result = apply(source, tmp_path)
    db.bundle["drafts"][0]["revision"] = 3
    with pytest.raises(ValueError, match="changed"):
        module.undo_sync(DSN, source, tmp_path/"receipt.json", result["receipt_sha256"])
    assert len(db.bundle["drafts"]) == 2


def test_tampered_receipt_is_not_used(setup, tmp_path):
    source, db = setup
    result = apply(source, tmp_path)
    (tmp_path/"receipt.json").write_text("{}")
    with pytest.raises(ValueError, match="receipt checksum"):
        module.undo_sync(DSN, source, tmp_path/"receipt.json", result["receipt_sha256"])


def test_remote_development_import_refused(setup):
    source, _ = setup
    with pytest.raises(ValueError):
        module.sync_drafts("dbname=campaigniq_dev host=remote.example", source)


def test_render_development_import_refused(setup, monkeypatch):
    source, _ = setup
    monkeypatch.setenv("RENDER", "true")
    with pytest.raises(ValueError, match="Render"):
        module.sync_drafts(DSN, source)


def test_wrong_server_database_refuses_writes(setup, tmp_path):
    source, db = setup
    db.dbname = "production"
    with pytest.raises(ValueError, match="database"):
        apply(source, tmp_path)
    assert not any(sql.startswith("INSERT") for sql, _ in db.calls)


def test_cli_sanitizes_connection_errors(monkeypatch, capsys):
    monkeypatch.setenv("CAMPAIGNIQ_DRAFT_EXPORT_DATABASE_URL", "password=secret")
    def fail(*args): raise RuntimeError("password=secret")
    monkeypatch.setattr(module, "export_drafts", fail)
    assert module.main(["export", "--read-only", "--months", "2025-05",
                        "--output", "unused"]) == 1
    output = capsys.readouterr().out
    assert "secret" not in output
    assert not json.loads(output)["publication_authorized"]


def test_dry_run_reports_conflicting_months_without_changing_them(setup):
    source, db = setup
    db.bundle = copy.deepcopy(source)
    db.bundle["drafts"][0]["revision"] = 3
    result = module.sync_drafts(DSN, source)
    assert result["conflicting_months"] == ["2025-05"]
    assert not result["applied"]
    assert db.bundle["drafts"][0]["revision"] == 3


def test_verification_failure_rolls_back_inserted_rows(setup, tmp_path, monkeypatch):
    source, db = setup
    read = module._read_rows
    count = 0
    def corrupted(connection, months):
        nonlocal count
        count += 1
        result = read(connection, months)
        if count == 2:
            result = copy.deepcopy(result)
            result["drafts"][0]["revision"] = 3
        return result
    monkeypatch.setattr(module, "_read_rows", corrupted)
    with pytest.raises(ValueError, match="byte-for-byte"):
        apply(source, tmp_path)
    assert db.bundle == empty(source)
    assert db.calls[-1][0] == "ROLLBACK"


def test_different_timestamp_offsets_normalize_to_same_moment(setup):
    source, db = setup
    db.bundle = copy.deepcopy(source)
    for draft in db.bundle["drafts"]:
        draft["created_at"] = "2026-10-22T05:00:00-07:00"
        draft["updated_at"] = "2026-10-22T05:00:00-07:00"
    report = module.sync_drafts(DSN, source)
    assert not report["conflicting_months"]
    assert report["identical_months"] == source["months"]
