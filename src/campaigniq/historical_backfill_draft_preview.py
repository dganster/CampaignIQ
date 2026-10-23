"""Bind a read-only monthly preview to saved draft bytes and frozen storage.

Developer API only. Neither a publication workflow nor proof of source completeness.
Workspace must come from authenticated server-side context.
"""
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType

from campaigniq.import_contract import MonthlyInputRole
from campaigniq.import_draft_bridge import restore_uploads, assert_revision_unchanged
from campaigniq.import_preflight import prepare_monthly_import
from campaigniq.monthly_import_execution import execute_monthly_import
from campaigniq.pdf_text import extract_pdf_text
from campaigniq.historical_backfill_snapshot import capture_snapshot, verify_snapshot
from campaigniq.historical_backfill_replay import ReplayPeriod, _book, _canonical
from campaigniq.persistence.lot_book_store import serialize_lot_book


@dataclass(frozen=True)
class BoundDraftPreview:
    report: dict
    replay_period: ReplayPeriod


class _FrozenStorage:
    def __init__(self, snapshot):
        self.namespace = snapshot["workspace"]
        self._artifacts = MappingProxyType(dict(snapshot["artifacts"]))

    def exists(self, key):
        return key in self._artifacts

    def read_text(self, key):
        return self._artifacts[key]

    def list_keys(self, *, prefix="", suffix=""):
        return tuple(sorted(k for k in self._artifacts
                            if k.startswith(prefix) and k.endswith(suffix)))

    def write_text(self, *args, **kwargs):
        raise RuntimeError("preview attempted an artifact write")

    def delete(self, *args, **kwargs):
        raise RuntimeError("preview attempted an artifact deletion")


def _document_manifest(uploads):
    return {
        str(role.value if isinstance(role, MonthlyInputRole) else role): {
            "filename": upload.name, "media_type": upload.media_type,
            "sha256": sha256(upload.getvalue()).hexdigest(),
            "byte_size": len(upload.getvalue())}
        for role, upload in uploads.items()
    }


def preview_saved_draft_for_replay(
    repository, draft_id, storage, *, reference_closing_text,
    forex_applicable=True, crypto_applicable=True, historical_evidence=None,
):
    """Restore, validate and preview a saved draft with existing production readers.

    reference_closing_text is an independently obtained comparison state supplied
    by the caller, NOT one manufactured from this preview. Its authenticity and
    completeness remain unverified. Reconciliation exceptions are not accepted
    by this API. No schemas, publication markers or retained files are written.
    """
    draft = repository.get(draft_id)
    if (draft.id != draft_id or not repository.workspace
            or draft.workspace != repository.workspace
            or storage.namespace != draft.workspace):
        raise ValueError("draft/storage workspace mismatch")
    if type(forex_applicable) is not bool or type(crypto_applicable) is not bool:
        raise ValueError("market applicability must be explicit booleans")
    snapshot = capture_snapshot(storage)
    frozen = _FrozenStorage(snapshot)
    uploads = restore_uploads(repository, draft_id)
    documents = _document_manifest(uploads)
    assert_revision_unchanged(repository, draft_id, draft.revision)
    # Freeze caller-supplied ancillary histories as bytes, without reading paths.
    history = dict(historical_evidence or {})
    for name, content in history.items():
        if (not isinstance(name, str) or Path(name).name != name
                or name in {"", ".", ".."} or "\\" in name
                or not isinstance(content, bytes) or not content):
            raise ValueError("historical evidence must have safe filenames and nonempty bytes")
    with TemporaryDirectory(prefix="campaigniq-backfill-preview-") as directory:
        root = Path(directory)
        supplied = _prepare_uploads(upload_dir=root, uploads=uploads)
        source_hashes = {role.value: sha256(path.read_bytes()).hexdigest()
                         for role, path in supplied.items()}
        history_root = root / "history"
        history_root.mkdir()
        for name, content in history.items():
            (history_root / name).write_bytes(content)
        preflight = prepare_monthly_import(
            draft.year, draft.month, authoritative_state_root=root / "state",
            supplied_inputs=supplied, artifact_storage=frozen,
            forex_applicable=forex_applicable, crypto_applicable=crypto_applicable)
        if not preflight.ready or preflight.opening_lot_book is None:
            failures = [v.message for v in preflight.validations if not v.valid]
            raise ValueError("Draft preflight not ready: " + "; ".join(failures))
        end = preflight.contract.period_end
        previous = preflight.contract.period_start - date.resolution
        reference = _book(reference_closing_text, end)
        original_opening = serialize_lot_book(
            period_end=previous, lot_book=preflight.opening_lot_book)
        execution = execute_monthly_import(
            preflight, authoritative_state_root=root / "state",
            supplied_inputs=supplied, historical_source_root=history_root,
            artifact_storage=frozen, preview_only=True)
        if execution.finalized:
            raise RuntimeError("preview unexpectedly finalized")
        result = execution.result
        opening = serialize_lot_book(period_end=previous, lot_book=result.opening_lot_book)
        closing = serialize_lot_book(period_end=end, lot_book=result.ending_lot_book)
        # Strict state validation also rejects non-finite values and duplicate IDs.
        _book(opening, previous)
        _book(closing, end)
        if source_hashes != {role.value: sha256(path.read_bytes()).hexdigest()
                             for role, path in supplied.items()}:
            raise ValueError("prepared source bytes changed during preview")
        if any((history_root / name).read_bytes() != content
               for name, content in history.items()):
            raise ValueError("historical evidence changed during preview")
    if documents != _document_manifest(restore_uploads(repository, draft_id)):
        raise ValueError("draft document bytes changed during preview")
    assert_revision_unchanged(repository, draft_id, draft.revision)
    if not verify_snapshot(storage, snapshot)["verified"]:
        raise ValueError("artifact storage changed during preview")
    boundary = result.boundary_reconstruction
    blockers = []
    mismatches = [
        {"instrument": repr(m.instrument), "computed_quantity": str(m.computed_quantity),
         "snapshot_quantity": str(m.snapshot_quantity)}
        for m in execution.closing_reconciliation.mismatches]
    if mismatches:
        blockers.append("Closing inventory is unreconciled; no exception was approved.")
    if opening != original_opening:
        blockers.append("Pipeline adjusted opening lot provenance; independent boundary review required.")
    matches_reference = closing == _canonical(reference, end)
    if not matches_reference:
        blockers.append("Computed closing lots differ from the supplied reference state.")
    if boundary.unresolved_positions or boundary.unresolved_campaigns or boundary.historical_requirements:
        blockers.append("Historical boundary evidence remains incomplete.")
    binding = {
        "workspace": draft.workspace, "draft_id": str(draft_id),
        "draft_revision": draft.revision, "period_end": end.isoformat(),
        "documents": documents, "prepared_source_sha256": source_hashes,
        "historical_evidence_sha256": {n: sha256(b).hexdigest() for n, b in sorted(history.items())},
        "storage_sha256": sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest(),
        "opening_sha256": sha256(opening.encode()).hexdigest(),
        "computed_closing_sha256": sha256(closing.encode()).hexdigest(),
        "reference_closing_sha256": sha256(reference_closing_text.encode()).hexdigest(),
        "forex_applicable": forex_applicable, "crypto_applicable": crypto_applicable,
    }
    report = {
        "format": "campaigniq.historical_backfill_draft_preview", "version": 1,
        "binding": binding,
        "binding_sha256": sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest(),
        "closing_reconciled": execution.closing_reconciliation.reconciled,
        "closing_mismatches": mismatches, "matches_reference": matches_reference,
        "blocking_findings": blockers, "source_bytes_bound": True,
        "evidence_completeness_verified": False, "publication_authorized": False,
        "limitation": "Diagnostic only. Requires independent source completeness, full downstream replay, reviewed decisions, backup verification and transactional authorization.",
    }
    return BoundDraftPreview(report, ReplayPeriod(
        end, opening, reference_closing_text, result.position_history, result.campaigns))


def _prepare_uploads(*, upload_dir, uploads):
    supplied = {}

    tos_upload = uploads.get(MonthlyInputRole.THINKORSWIM_TRADE_HISTORY)
    if tos_upload is not None:
        suffix = Path(tos_upload.name).suffix
        tos_path = upload_dir / f"thinkorswim_trade_history{suffix}"
        tos_path.write_bytes(tos_upload.getvalue())
        supplied[MonthlyInputRole.THINKORSWIM_TRADE_HISTORY] = tos_path

    schwab_upload = uploads.get("schwab_brokerage_statement")
    if schwab_upload is not None:
        suffix = Path(schwab_upload.name).suffix
        schwab_path = upload_dir / f"schwab_brokerage_statement{suffix}"
        schwab_path.write_bytes(schwab_upload.getvalue())

        schwab_source_path = schwab_path
        if suffix.lower() == ".pdf":
            schwab_source_path = extract_pdf_text(
                schwab_path,
                upload_dir / "schwab_brokerage_statement.txt",
            )

        # The Brokerage Statement supplies closing positions and any
        # assignment/exercise evidence needed by the existing Schwab readers.
        supplied[
            MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT
        ] = schwab_source_path
        supplied[
            MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE
        ] = schwab_source_path

    opening_upload = uploads.get(
        MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT
    )
    if opening_upload is not None:
        suffix = Path(opening_upload.name).suffix
        opening_path = upload_dir / f"schwab_opening_position_snapshot{suffix}"
        opening_path.write_bytes(opening_upload.getvalue())

        opening_source_path = opening_path
        if suffix.lower() == ".pdf":
            opening_source_path = extract_pdf_text(
                opening_path,
                upload_dir / "schwab_opening_position_snapshot.txt",
            )

        supplied[
            MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT
        ] = opening_source_path

    realized_upload = uploads.get(MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS)
    if realized_upload is not None:
        suffix = Path(realized_upload.name).suffix
        realized_path = upload_dir / f"schwab_realized_gain_loss{suffix}"
        realized_path.write_bytes(realized_upload.getvalue())

        realized_source_path = realized_path
        if suffix.lower() == ".pdf":
            realized_source_path = extract_pdf_text(
                realized_path,
                upload_dir / "schwab_realized_gain_loss.txt",
            )

        supplied[
            MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS
        ] = realized_source_path

    forex_upload = uploads.get(MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT)
    if forex_upload is not None:
        suffix = Path(forex_upload.name).suffix
        forex_path = upload_dir / f"schwab_forex_transaction_report{suffix}"
        forex_path.write_bytes(forex_upload.getvalue())
        supplied[MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT] = forex_path

    next_upload = uploads.get(MonthlyInputRole.SCHWAB_NEXT_MONTH_ASSIGNMENT_EVIDENCE)
    if next_upload is not None:
        suffix = Path(next_upload.name).suffix
        next_path = upload_dir / f"next_month_assignment_evidence{suffix}"
        next_path.write_bytes(next_upload.getvalue())
        if suffix.lower() == ".pdf":
            next_path = extract_pdf_text(next_path, upload_dir / "next_month_assignment_evidence.txt")
        supplied[MonthlyInputRole.SCHWAB_NEXT_MONTH_ASSIGNMENT_EVIDENCE] = next_path

    crypto_upload = uploads.get(MonthlyInputRole.SCHWAB_CRYPTO_STATEMENT)
    if crypto_upload is not None:
        suffix = Path(crypto_upload.name).suffix
        crypto_path = upload_dir / f"schwab_crypto_statement{suffix}"
        crypto_path.write_bytes(crypto_upload.getvalue())
        if suffix.lower() == ".pdf":
            crypto_path = extract_pdf_text(crypto_path, upload_dir / "schwab_crypto_statement.txt")
        supplied[MonthlyInputRole.SCHWAB_CRYPTO_STATEMENT] = crypto_path

    return supplied
