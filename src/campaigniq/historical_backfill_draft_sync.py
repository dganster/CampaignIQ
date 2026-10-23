"""Export saved drafts read-only; add missing drafts to local development only."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import UUID

from campaigniq.historical_backfill_inspect import _connection_parameters

WORKSPACE = "workspace-dennis"
MONTHS = {f"2025-{m:02d}" for m in range(1, 7)}
FORMAT = "campaigniq.backfill_draft_bundle"
MAX_BYTES = 300 * 1024 * 1024
DRAFT_FIELDS = "id,workspace,year,month,state,revision,created_at,updated_at"
DOCUMENT_FIELDS = "id,draft_id,role,filename,media_type,sha256,content,revision,uploaded_at,superseded"


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(content):
    return sha256(content).hexdigest()


def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("invalid UUID")


def _timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timezone required")


def validate_bundle(bundle):
    if (not isinstance(bundle, dict)
            or set(bundle) != {"format", "version", "workspace", "months", "drafts", "documents"}
            or bundle["format"] != FORMAT or type(bundle["version"]) is not int
            or bundle["version"] != 1 or bundle["workspace"] != WORKSPACE):
        raise ValueError("invalid draft bundle envelope")
    months = bundle["months"]
    if (not isinstance(months, list) or not months or any(not isinstance(m, str) for m in months)
            or len(set(months)) != len(months) or not set(months) <= MONTHS):
        raise ValueError("invalid export months")
    if not isinstance(bundle["drafts"], list) or not isinstance(bundle["documents"], list):
        raise ValueError("invalid draft bundle rows")
    drafts, seen_months = {}, set()
    for row in bundle["drafts"]:
        if set(row) != set(DRAFT_FIELDS.split(",")):
            raise ValueError("invalid draft fields")
        _uuid(row["id"])
        if (row["workspace"] != WORKSPACE or row["state"] != "draft"
                or type(row["year"]) is not int or type(row["month"]) is not int
                or type(row["revision"]) is not int or row["revision"] < 0):
            raise ValueError("invalid draft metadata")
        month = f'{row["year"]:04d}-{row["month"]:02d}'
        if month not in months or month in seen_months or row["id"] in drafts:
            raise ValueError("duplicate or out-of-scope draft")
        _timestamp(row["created_at"])
        _timestamp(row["updated_at"])
        seen_months.add(month)
        drafts[row["id"]] = row
    seen_ids, current_roles = set(), set()
    for row in bundle["documents"]:
        if set(row) != (set(DOCUMENT_FIELDS.split(",")) - {"content"}) | {"content_base64", "byte_size"}:
            raise ValueError("invalid document fields")
        _uuid(row["id"])
        if row["draft_id"] not in drafts or row["id"] in seen_ids:
            raise ValueError("orphan or duplicate document")
        if (not isinstance(row["role"], str) or not 1 <= len(row["role"]) <= 128
                or not isinstance(row["filename"], str) or not 1 <= len(row["filename"]) <= 255
                or row["media_type"] not in {"application/pdf", "text/csv", "text/plain", "application/vnd.ms-excel"}
                or type(row["revision"]) is not int
                or not 1 <= row["revision"] <= drafts[row["draft_id"]]["revision"]
                or type(row["superseded"]) is not bool):
            raise ValueError("invalid document metadata")
        _timestamp(row["uploaded_at"])
        if type(row["byte_size"]) is not int or not 1 <= row["byte_size"] <= 20 * 1024 * 1024:
            raise ValueError("invalid document size")
        content = base64.b64decode(row["content_base64"], validate=True)
        if len(content) != row["byte_size"] or _digest(content) != row["sha256"]:
            raise ValueError("document checksum mismatch")
        current = (row["draft_id"], row["role"])
        if not row["superseded"]:
            if current in current_roles:
                raise ValueError("duplicate current document role")
            current_roles.add(current)
        seen_ids.add(row["id"])
    return bundle


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def read_bundle(path, expected_sha256):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("bundle exceeds size limit")
    content = path.read_bytes()
    if len(content) > MAX_BYTES or _digest(content) != expected_sha256:
        raise ValueError("bundle file checksum mismatch")
    return validate_bundle(json.loads(content, object_pairs_hook=_unique_pairs))


def _write_private(path, content):
    # Exclusive creation preserves every existing export, backup and receipt.
    path = Path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != content:
            raise ValueError("output verification failed")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return _digest(content)


def write_bundle(path, bundle):
    validate_bundle(bundle)
    content = (_json(bundle) + "\n").encode()
    if len(content) > MAX_BYTES:
        raise ValueError("bundle exceeds size limit")
    return _write_private(path, content)


def _read_rows(connection, months):
    # All document history is included, not only the current versions.
    month_numbers = [int(m[-2:]) for m in sorted(months)]
    drafts = connection.execute(
        f"SELECT {DRAFT_FIELDS} FROM public.import_drafts "
        "WHERE workspace=%s AND year=2025 AND month=ANY(%s) AND state='draft' ORDER BY month",
        (WORKSPACE, month_numbers)).fetchall()
    documents = connection.execute(
        "SELECT " + ",".join("d." + f for f in DOCUMENT_FIELDS.split(",")) +
        " FROM public.import_draft_documents d JOIN public.import_drafts p ON p.id=d.draft_id "
        "WHERE p.workspace=%s AND p.year=2025 AND p.month=ANY(%s) AND p.state='draft' "
        "ORDER BY d.draft_id,d.revision,d.id", (WORKSPACE, month_numbers)).fetchall()
    result = {"format": FORMAT, "version": 1, "workspace": WORKSPACE,
              "months": sorted(months), "drafts": [], "documents": []}
    for values in drafts:
        row = dict(zip(DRAFT_FIELDS.split(","), values, strict=True))
        row["id"] = str(row["id"])
        for field in ("created_at", "updated_at"):
            row[field] = row[field].astimezone(timezone.utc).isoformat()
        result["drafts"].append(row)
    for values in documents:
        row = dict(zip(DOCUMENT_FIELDS.split(","), values, strict=True))
        row["id"], row["draft_id"] = str(row["id"]), str(row["draft_id"])
        row["uploaded_at"] = row["uploaded_at"].astimezone(timezone.utc).isoformat()
        content = bytes(row.pop("content"))
        row.update(content_base64=base64.b64encode(content).decode(), byte_size=len(content))
        result["documents"].append(row)
    return validate_bundle(result)


def _verify_transaction(connection, readonly, local=False):
    row = connection.execute(
        "SELECT current_database(), current_setting('transaction_read_only'), "
        "current_setting('transaction_isolation')").fetchone()
    if row[1:] != ("on" if readonly else "off",
                   "repeatable read" if readonly else "serializable"):
        raise ValueError("transaction mode verification failed")
    if local and row[0] != "campaigniq_dev":
        raise ValueError("development database verification failed")


def export_drafts(dsn, months, output):
    if not months or not set(months) <= MONTHS:
        raise ValueError("export limited to January–June 2025")
    import psycopg
    # A remote source is permitted only for this read-only export. This function
    # contains no database writes, schema setup, publication or import calls.
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10,
                         options="-c default_transaction_read_only=on -c statement_timeout=30000") as connection:
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            _verify_transaction(connection, True)
            bundle = _read_rows(connection, months)
            found = {f'{r["year"]:04d}-{r["month"]:02d}' for r in bundle["drafts"]}
            if found != set(months):
                raise ValueError("one or more selected active drafts were not found")
        finally:
            connection.execute("ROLLBACK")
    digest = write_bundle(output, bundle)
    return {"exported_months": sorted(months), "drafts": len(bundle["drafts"]),
            "documents_including_history": len(bundle["documents"]),
            "sha256": digest, "source_read_only": True, "publication_authorized": False}


def _draft_documents(bundle, draft_id):
    return sorted((r for r in bundle["documents"] if r["draft_id"] == draft_id),
                  key=lambda r: r["id"])


def plan_import(source, target):
    validate_bundle(source)
    validate_bundle(target)
    if source["months"] != target["months"]:
        raise ValueError("source/target month selection differs")
    existing = {(d["year"], d["month"]): d for d in target["drafts"]}
    additions, matches, conflicts = [], [], []
    for draft in source["drafts"]:
        local = existing.get((draft["year"], draft["month"]))
        month = f'{draft["year"]:04d}-{draft["month"]:02d}'
        if local is None:
            additions.append(draft["id"])
        elif local == draft and _draft_documents(source, draft["id"]) == _draft_documents(target, local["id"]):
            matches.append(month)
        else:
            conflicts.append(month)
    return {"insert_draft_ids": additions, "identical_months": matches,
            "conflicting_months": conflicts, "publication_authorized": False}


def _check_ids(connection, source, additions):
    draft_ids = [UUID(i) for i in additions]
    document_ids = [UUID(d["id"]) for d in source["documents"] if d["draft_id"] in additions]
    if draft_ids and connection.execute(
        "SELECT id FROM public.import_drafts WHERE id=ANY(%s)", (draft_ids,)).fetchall():
        raise ValueError("draft UUID already exists outside the matching active month")
    if document_ids and connection.execute(
        "SELECT id FROM public.import_draft_documents WHERE id=ANY(%s)", (document_ids,)).fetchall():
        raise ValueError("document UUID already exists")


def sync_drafts(dsn, source, *, apply=False, backup=None, receipt=None):
    validate_bundle(source)
    if os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"):
        raise ValueError("development import prohibited on Render")
    if apply and (not backup or not receipt or Path(backup).resolve() == Path(receipt).resolve()):
        raise ValueError("apply requires distinct new backup and receipt paths")
    parameters = _connection_parameters(dsn)
    if apply:
        parameters["options"] = "-c default_transaction_read_only=off -c lock_timeout=10000 -c statement_timeout=30000"
    import psycopg
    with psycopg.connect(**parameters, autocommit=True) as connection:
        connection.execute("BEGIN ISOLATION LEVEL SERIALIZABLE" if apply else
                           "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        committed = False
        try:
            _verify_transaction(connection, not apply, local=True)
            if apply:
                # Table locks protect even against writers that do not use our
                # advisory conventions. They affect only the LOCAL draft tables.
                connection.execute(
                    "LOCK TABLE public.import_drafts, public.import_draft_documents IN SHARE ROW EXCLUSIVE MODE")
            target = _read_rows(connection, source["months"])
            plan = plan_import(source, target)
            if plan["conflicting_months"]:
                if not apply:
                    return dict(plan, applied=False)
                raise ValueError("existing local drafts differ; import refused")
            additions = plan["insert_draft_ids"]
            _check_ids(connection, source, additions)
            if not apply:
                return dict(plan, applied=False)
            backup_hash = write_bundle(backup, target)
            read_bundle(backup, backup_hash)
            record = {"format": "campaigniq.backfill_draft_sync_receipt", "version": 1,
                      "workspace": WORKSPACE, "database": "campaigniq_dev",
                      "source_sha256": _digest((_json(source) + "\n").encode()),
                      "backup_sha256": backup_hash, "insert_draft_ids": additions}
            receipt_hash = _write_private(receipt, (_json(record) + "\n").encode())
            for row in source["drafts"]:
                if row["id"] not in additions:
                    continue
                connection.execute(
                    f"INSERT INTO public.import_drafts ({DRAFT_FIELDS}) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    tuple(row[f] for f in DRAFT_FIELDS.split(",")))
            for row in source["documents"]:
                if row["draft_id"] not in additions:
                    continue
                values = dict(row, content=base64.b64decode(row["content_base64"], validate=True))
                connection.execute(
                    f"INSERT INTO public.import_draft_documents ({DOCUMENT_FIELDS}) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    tuple(values[f] for f in DOCUMENT_FIELDS.split(",")))
            expected = dict(source, drafts=target["drafts"] + [r for r in source["drafts"] if r["id"] in additions],
                            documents=target["documents"] + [r for r in source["documents"] if r["draft_id"] in additions])
            actual = _read_rows(connection, source["months"])
            if (_json(sorted(actual["drafts"], key=lambda r: r["id"])) !=
                    _json(sorted(expected["drafts"], key=lambda r: r["id"]))
                    or _json(sorted(actual["documents"], key=lambda r: r["id"])) !=
                    _json(sorted(expected["documents"], key=lambda r: r["id"]))):
                raise ValueError("inserted drafts failed byte-for-byte verification")
            connection.execute("COMMIT")
            committed = True
            return dict(plan, applied=True, backup_sha256=backup_hash, receipt_sha256=receipt_hash)
        finally:
            if not committed:
                connection.execute("ROLLBACK")


def undo_sync(dsn, source, receipt_path, receipt_sha256):
    """Remove only receipt-listed inserted drafts if every retained row still matches."""
    validate_bundle(source)
    if os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"):
        raise ValueError("development rollback prohibited on Render")
    raw_receipt = Path(receipt_path).read_bytes()
    if len(raw_receipt) > 1024 * 1024 or _digest(raw_receipt) != receipt_sha256:
        raise ValueError("rollback receipt checksum mismatch")
    record = json.loads(raw_receipt, object_pairs_hook=_unique_pairs)
    if (record.get("format") != "campaigniq.backfill_draft_sync_receipt"
            or record.get("version") != 1 or record.get("workspace") != WORKSPACE
            or record.get("database") != "campaigniq_dev"
            or record.get("source_sha256") != _digest((_json(source) + "\n").encode())):
        raise ValueError("rollback receipt does not match source")
    ids = record.get("insert_draft_ids")
    source_rows = {r["id"]: r for r in source["drafts"]}
    if not isinstance(ids, list) or len(set(ids)) != len(ids) or not set(ids) <= source_rows.keys():
        raise ValueError("invalid rollback draft IDs")
    parameters = _connection_parameters(dsn)
    parameters["options"] = "-c default_transaction_read_only=off -c lock_timeout=10000 -c statement_timeout=30000"
    import psycopg
    with psycopg.connect(**parameters, autocommit=True) as connection:
        connection.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
        committed = False
        try:
            _verify_transaction(connection, False, local=True)
            connection.execute("LOCK TABLE public.import_drafts, public.import_draft_documents IN SHARE ROW EXCLUSIVE MODE")
            target = _read_rows(connection, source["months"])
            target_rows = {r["id"]: r for r in target["drafts"]}
            removable = []
            for draft_id in ids:
                if draft_id not in target_rows:
                    # Missing active row may have been abandoned or moved: refuse
                    # if its UUID still exists anywhere.
                    if connection.execute("SELECT id FROM public.import_drafts WHERE id=%s",
                                          (UUID(draft_id),)).fetchone() is not None:
                        raise ValueError("imported draft changed; rollback refused")
                    continue
                if (target_rows[draft_id] != source_rows[draft_id]
                        or _draft_documents(target, draft_id) != _draft_documents(source, draft_id)):
                    raise ValueError("imported draft changed; rollback refused")
                removable.append(UUID(draft_id))
            for draft_id in removable:
                connection.execute("DELETE FROM public.import_draft_documents WHERE draft_id=%s", (draft_id,))
                connection.execute("DELETE FROM public.import_drafts WHERE id=%s AND workspace=%s",
                                   (draft_id, WORKSPACE))
            connection.execute("COMMIT")
            committed = True
            return {"removed_imported_drafts": len(removable), "publication_authorized": False}
        finally:
            if not committed:
                connection.execute("ROLLBACK")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--read-only", required=True, action="store_true")
    export.add_argument("--months", nargs="+", required=True, choices=sorted(MONTHS))
    export.add_argument("--output", required=True)
    for command in ("import", "undo"):
        child = sub.add_parser(command)
        child.add_argument("--development-only", required=True, action="store_true")
        child.add_argument("--bundle", required=True)
        child.add_argument("--expected-sha256", required=True)
        if command == "import":
            child.add_argument("--apply", action="store_true")
            child.add_argument("--backup")
            child.add_argument("--receipt")
        else:
            child.add_argument("--apply", required=True, action="store_true")
            child.add_argument("--receipt", required=True)
            child.add_argument("--receipt-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            dsn = os.environ.get("CAMPAIGNIQ_DRAFT_EXPORT_DATABASE_URL")
            if not dsn:
                raise ValueError("export connection setting missing")
            report = export_drafts(dsn, args.months, args.output)
        else:
            dsn = os.environ.get("CAMPAIGNIQ_DEV_DATABASE_URL")
            if not dsn:
                raise ValueError("development connection setting missing")
            source = read_bundle(args.bundle, args.expected_sha256)
            if args.command == "undo":
                report = undo_sync(dsn, source, args.receipt, args.receipt_sha256)
            else:
                report = sync_drafts(dsn, source, apply=args.apply, backup=args.backup, receipt=args.receipt)
    except Exception as exc:
        # Connection exceptions can contain credentials; never echo their text.
        print(json.dumps({"error": type(exc).__name__, "message": "Draft transfer stopped; no publication authorized.",
                          "publication_authorized": False}))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
