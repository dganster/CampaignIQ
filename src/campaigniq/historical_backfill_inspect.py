"""Inspect January–June 2025 drafts in a local development database; never publish."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType

from campaigniq.historical_backfill_continuity import assess_continuity
from campaigniq.historical_backfill_draft_preview import (
    _FrozenStorage, _prepare_uploads, preview_saved_draft_for_replay,
)
from campaigniq.historical_backfill_impact import assess_storage
from campaigniq.historical_backfill_snapshot import capture_snapshot
from campaigniq.import_draft_bridge import SUPPORTED_UPLOAD_KEYS, restore_uploads
from campaigniq.import_preflight import prepare_monthly_import
from campaigniq.persistence.import_drafts import Draft
from campaigniq.persistence.monthly_publication import is_month_published_in_storage

WORKSPACE = "workspace-dennis"
MONTHS = tuple(f"2025-{month:02d}" for month in range(1, 7))


@dataclass(frozen=True)
class InspectionSnapshot:
    repository: object
    storage: object
    integrity_issues: dict


class _Drafts:
    """Immutable offline implementation of only the draft read contract."""
    def __init__(self, workspace, drafts, documents):
        self.workspace = workspace
        self._drafts = MappingProxyType(dict(drafts))
        self._documents = MappingProxyType(dict(documents))

    def get(self, draft_id):
        if draft_id not in self._drafts:
            raise FileNotFoundError("draft not found")
        return self._drafts[draft_id]

    def list_drafts(self):
        return tuple(sorted(self._drafts.values(), key=lambda d: (d.year, d.month)))

    def document(self, draft_id, role):
        self.get(draft_id)
        try:
            return self._documents[draft_id, role]
        except KeyError as exc:
            raise FileNotFoundError("document not found") from exc


def _connection_parameters(dsn):
    from psycopg.conninfo import conninfo_to_dict
    try:
        parameters = conninfo_to_dict(dsn)
    except Exception as exc:
        raise ValueError("invalid development database connection configuration") from exc
    if parameters.get("dbname") != "campaigniq_dev" or parameters.get("service"):
        raise ValueError("explicit campaigniq_dev database required; services are prohibited")
    host = parameters.get("host", "localhost")
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("only a local TCP development database is permitted")
    expected_address = "::1" if host == "::1" else "127.0.0.1"
    if parameters.get("hostaddr", expected_address) != expected_address:
        raise ValueError("remote or conflicting hostaddr is prohibited")
    parameters.update(host=host, hostaddr=expected_address, dbname="campaigniq_dev",
                      options="-c default_transaction_read_only=on",
                      connect_timeout="10")
    # Explicit options override ambient libpq service/host routing.
    return parameters


def load_development_snapshot(dsn, workspace):
    """Read all required rows from one server-enforced read-only MVCC snapshot."""
    if workspace != WORKSPACE:
        raise ValueError("this development runner requires workspace-dennis")
    if os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"):
        raise ValueError("runner prohibited on Render")
    parameters = _connection_parameters(dsn)
    import psycopg
    with psycopg.connect(**parameters, autocommit=True) as connection:
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            identity = connection.execute(
                "SELECT current_database(), current_setting('transaction_read_only'), "
                "current_setting('transaction_isolation')").fetchone()
            if identity != ("campaigniq_dev", "on", "repeatable read"):
                raise ValueError("development read-only transaction verification failed")
            artifacts = connection.execute(
                "SELECT artifact_key, content FROM public.artifact_storage_dev "
                "WHERE namespace=%s ORDER BY artifact_key", (workspace,)).fetchall()
            draft_rows = connection.execute(
                "SELECT id,workspace,year,month,revision FROM public.import_drafts "
                "WHERE workspace=%s AND state='draft' AND year=2025 "
                "AND month BETWEEN 1 AND 6 ORDER BY month", (workspace,)).fetchall()
            document_rows = connection.execute(
                "SELECT d.draft_id,d.role,d.filename,d.media_type,d.content,d.sha256,d.revision "
                "FROM public.import_draft_documents d "
                "JOIN public.import_drafts p ON p.id=d.draft_id "
                "WHERE p.workspace=%s AND p.state='draft' AND p.year=2025 "
                "AND p.month BETWEEN 1 AND 6 AND d.superseded=false "
                "ORDER BY d.draft_id,d.role", (workspace,)).fetchall()
        finally:
            connection.execute("ROLLBACK")
    drafts = {}
    months = set()
    for row in draft_rows:
        draft = Draft(*row)
        if (draft.workspace != workspace or draft.year != 2025
                or not 1 <= draft.month <= 6 or draft.month in months or draft.id in drafts
                or type(draft.revision) is not int or draft.revision < 0):
            raise ValueError("invalid or duplicate draft metadata")
        drafts[draft.id] = draft
        months.add(draft.month)
    documents, issues = {}, {}
    supported = {role.value if hasattr(role, "value") else role
                 for role in SUPPORTED_UPLOAD_KEYS}
    for draft_id, role, filename, media_type, content, digest, revision in document_rows:
        if draft_id not in drafts:
            raise ValueError("document outside selected workspace drafts")
        if (draft_id, role) in documents:
            raise ValueError("duplicate current document role")
        content = bytes(content)
        documents[draft_id, role] = (filename, media_type, content)
        findings = issues.setdefault(draft_id, [])
        if not content or sha256(content).hexdigest() != digest:
            findings.append(f"{role}: original document checksum mismatch or empty content")
        if type(revision) is not int or not 1 <= revision <= drafts[draft_id].revision:
            findings.append(f"{role}: invalid document revision")
        if role not in supported:
            findings.append(f"{role}: unsupported saved role requires review")
    if len(artifacts) != len(dict(artifacts)):
        raise ValueError("duplicate artifact keys")
    storage = _FrozenStorage({"workspace": workspace, "artifacts": dict(artifacts)})
    return InspectionSnapshot(_Drafts(workspace, drafts, documents), storage, issues)


def inspect_snapshot(snapshot, *, forex_not_applicable=(), crypto_not_applicable=()):
    """Diagnostic report only; never manufacture missing opening/closing states."""
    if not set(forex_not_applicable) <= set(MONTHS) or not set(crypto_not_applicable) <= set(MONTHS):
        raise ValueError("not-applicable choices must be January–June 2025")
    repository, storage = snapshot.repository, snapshot.storage
    if repository.workspace != WORKSPACE or storage.namespace != WORKSPACE:
        raise ValueError("inspection workspace mismatch")
    drafts = {f"{d.year:04d}-{d.month:02d}": d for d in repository.list_drafts()}
    baseline = capture_snapshot(storage)
    impact = assess_storage(storage, date(2025, 6, 30))
    continuity = assess_continuity(storage, date(2025, 6, 30))
    rows = []
    for month in MONTHS:
        draft = drafts.get(month)
        row = {"month": month, "draft_found": draft is not None,
               "publication_authorized": False, "blocking_findings": []}
        rows.append(row)
        if draft is None:
            row["blocking_findings"].append("No active saved draft in the development snapshot.")
            continue
        row.update(draft_id=str(draft.id), revision=draft.revision)
        uploads = restore_uploads(repository, draft.id)
        row["documents"] = [
            {"role": role.value if hasattr(role, "value") else role,
             "filename": upload.name, "byte_size": len(upload.content),
             "sha256": sha256(upload.content).hexdigest()}
            for role, upload in sorted(uploads.items(), key=lambda pair: str(pair[0]))]
        integrity = snapshot.integrity_issues.get(draft.id, [])
        if integrity:
            row["blocking_findings"].extend(integrity)
            continue
        forex = month not in forex_not_applicable
        crypto = month not in crypto_not_applicable
        row.update(forex_applicable=forex, crypto_applicable=crypto)
        try:
            with TemporaryDirectory(prefix="campaigniq-backfill-inspect-") as directory:
                root = Path(directory)
                supplied = _prepare_uploads(upload_dir=root, uploads=uploads)
                preflight = prepare_monthly_import(
                    draft.year, draft.month, authoritative_state_root=root / "state",
                    supplied_inputs=supplied, artifact_storage=storage,
                    forex_applicable=forex, crypto_applicable=crypto)
                row["preflight_ready"] = preflight.ready
                row["validations"] = [
                    {"role": v.role.value, "valid": v.valid, "message": v.message}
                    for v in preflight.validations]
                if not preflight.ready:
                    required = {r.role for r in preflight.contract.requirements if r.required}
                    row["blocking_findings"].extend(
                        v.message for v in preflight.validations
                        if v.role in required and not v.valid)
            key = f"{month}-lot-book.json"
            if (not storage.exists(key)
                    or not is_month_published_in_storage(storage, period_end=preflight.contract.period_end)):
                row["blocking_findings"].append(
                    "No independent published closing lot reference; preview/replay not run.")
                continue
            if not preflight.ready:
                continue
            preview = preview_saved_draft_for_replay(
                repository, draft.id, storage, reference_closing_text=storage.read_text(key),
                forex_applicable=forex, crypto_applicable=crypto)
            row["preview"] = preview.report
            row["blocking_findings"].extend(preview.report["blocking_findings"])
        except Exception as exc:
            # Diagnostic errors remain explicit blockers, never success or permission.
            row["blocking_findings"].append(f"Inspection failed: {type(exc).__name__}: {exc}")
    return {
        "format": "campaigniq.historical_backfill_inspection", "version": 1,
        "workspace": WORKSPACE, "database": "campaigniq_dev",
        "database_snapshot": "repeatable read, read only, rolled back",
        "storage_sha256": sha256(json.dumps(baseline, sort_keys=True).encode()).hexdigest(),
        "months": rows, "impact": impact, "continuity": continuity,
        "read_only": True, "publication_authorized": False,
        "evidence_completeness_verified": False,
        "conclusion": "Diagnostic only. Historical publication remains blocked.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development-only", action="store_true", required=True)
    parser.add_argument("--workspace", required=True, choices=[WORKSPACE])
    parser.add_argument("--forex-not-applicable", nargs="*", default=[], choices=MONTHS)
    parser.add_argument("--crypto-not-applicable", nargs="*", default=[], choices=MONTHS)
    args = parser.parse_args(argv)
    dsn = os.environ.get("CAMPAIGNIQ_DEV_DATABASE_URL")
    if not dsn:
        parser.error("set CAMPAIGNIQ_DEV_DATABASE_URL to the local campaigniq_dev database")
    try:
        snapshot = load_development_snapshot(dsn, args.workspace)
        report = inspect_snapshot(
            snapshot, forex_not_applicable=args.forex_not_applicable,
            crypto_not_applicable=args.crypto_not_applicable)
    except Exception as exc:
        # Never echo credentials, DSNs or connection exception text.
        print(json.dumps({"error": type(exc).__name__,
                          "message": "Development inspection failed; verify local database configuration and schema.",
                          "publication_authorized": False}))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
