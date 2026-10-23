from dataclasses import replace
from datetime import date
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import campaigniq.historical_backfill_draft_preview as module
from campaigniq.closing_inventory_reconciliation import (
    ClosingInventoryMismatch, ClosingInventoryReconciliation,
)
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.import_contract import MonthlyInputRole, monthly_import_contract
from campaigniq.persistence.import_drafts import Draft
from campaigniq.persistence.lot_book_store import serialize_lot_book
from campaigniq.monthly_import_execution import MonthlyImportExecution

END = date(2025, 6, 30)
ROLE = MonthlyInputRole.THINKORSWIM_TRADE_HISTORY.value


class Repo:
    workspace = "workspace-dennis"

    def __init__(self):
        self.draft = Draft(uuid4(), self.workspace, 2025, 6, 3)
        self.docs = {ROLE: ("../../source.csv", "text/csv", b"original trade bytes")}

    def get(self, draft_id):
        if draft_id != self.draft.id:
            raise FileNotFoundError("draft not found")
        return self.draft

    def document(self, draft_id, role):
        self.get(draft_id)
        if role not in self.docs:
            raise FileNotFoundError("document not found")
        return self.docs[role]


class Storage:
    namespace = "workspace-dennis"

    def __init__(self):
        self.items = {"2026-09-finalized.json": "published", "saved-draft": "untouched"}

    def list_keys(self):
        return tuple(self.items)

    def read_text(self, key):
        return self.items[key]

    def write_text(self, *args, **kwargs):
        pytest.fail("external storage write attempted")

    def delete(self, *args, **kwargs):
        pytest.fail("external storage deletion attempted")


def serialized(end=END):
    return serialize_lot_book(period_end=end, lot_book=LotBook())


@pytest.fixture
def context(monkeypatch):
    repo, storage = Repo(), Storage()
    paths = []
    boundary = SimpleNamespace(unresolved_positions=(), unresolved_campaigns=(),
                               historical_requirements=())
    result = SimpleNamespace(opening_lot_book=LotBook(), ending_lot_book=LotBook(),
                             position_history=PositionHistory(), campaigns=(),
                             boundary_reconstruction=boundary)
    state = {"on_execute": lambda **kwargs: None,
             "reconciliation": ClosingInventoryReconciliation(())}
    def prepare(year, month, **kwargs):
        assert (year, month) == (2025, 6)
        paths.append(Path(kwargs["authoritative_state_root"]).parent)
        assert kwargs["artifact_storage"].read_text("2026-09-finalized.json") == "published"
        return SimpleNamespace(ready=True, opening_lot_book=LotBook(),
                               contract=monthly_import_contract(year, month), validations=())
    def execute(preflight, **kwargs):
        assert kwargs["preview_only"] is True
        assert "reconciliation_decision" not in kwargs
        assert kwargs["artifact_storage"] is not storage
        assert kwargs["historical_source_root"].is_dir()
        state["on_execute"](**kwargs)
        return MonthlyImportExecution(result, state["reconciliation"], None)
    monkeypatch.setattr(module, "prepare_monthly_import", prepare)
    monkeypatch.setattr(module, "execute_monthly_import", execute)
    return repo, storage, paths, result, state


def run(context, **kwargs):
    repo, storage, *_ = context
    return module.preview_saved_draft_for_replay(
        repo, repo.draft.id, storage, reference_closing_text=serialized(), **kwargs)


def test_binds_original_document_revision_storage_and_prepared_bytes(context):
    repo, storage, paths, *_ = context
    before = dict(storage.items)
    output = run(context)
    binding = output.report["binding"]
    assert binding["draft_revision"] == 3
    assert binding["documents"][ROLE]["sha256"] == sha256(repo.docs[ROLE][2]).hexdigest()
    assert binding["prepared_source_sha256"][ROLE] == sha256(repo.docs[ROLE][2]).hexdigest()
    assert storage.items == before
    assert output.report["source_bytes_bound"]
    assert not output.report["publication_authorized"]
    assert not output.report["evidence_completeness_verified"]
    assert output.replay_period.period_end == END
    assert all(not path.exists() for path in paths)


@pytest.mark.parametrize("target", ["storage", "repository", "draft"])
def test_cross_workspace_rejected(context, target):
    repo, storage, *_ = context
    if target == "storage":
        storage.namespace = "workspace-andrew"
    elif target == "repository":
        repo.workspace = "workspace-andrew"
    else:
        repo.draft = replace(repo.draft, workspace="workspace-andrew")
    with pytest.raises(ValueError, match="workspace"):
        run(context)


def test_document_revision_change_during_preview_invalidates_result(context):
    repo, _, paths, _, state = context
    def change(**kwargs):
        repo.draft = replace(repo.draft, revision=4)
    state["on_execute"] = change
    with pytest.raises(ValueError, match="changed since validation"):
        run(context)
    assert all(not path.exists() for path in paths)


def test_bytes_changed_without_revision_also_rejected(context):
    repo, _, _, _, state = context
    def change(**kwargs):
        repo.docs[ROLE] = ("source.csv", "text/csv", b"tampered")
    state["on_execute"] = change
    with pytest.raises(ValueError, match="document bytes"):
        run(context)


def test_storage_changed_during_preview_rejected(context):
    _, storage, _, _, state = context
    state["on_execute"] = lambda **kwargs: storage.items.update({"2026-09-finalized.json": "changed"})
    with pytest.raises(ValueError, match="storage changed"):
        run(context)


def test_unreconciled_june_positions_remain_blocking(context):
    state = context[-1]
    from decimal import Decimal
    state["reconciliation"] = ClosingInventoryReconciliation((
        ClosingInventoryMismatch(Instrument("META"), Decimal("100"), Decimal("0")),))
    report = run(context).report
    assert not report["closing_reconciled"]
    assert report["closing_mismatches"][0]["computed_quantity"] == "100"
    assert any("unreconciled" in item for item in report["blocking_findings"])
    assert not report["publication_authorized"]


def test_prepared_sources_changed_by_pipeline_rejected(context):
    state = context[-1]
    def change(**kwargs):
        next(iter(kwargs["supplied_inputs"].values())).write_bytes(b"changed")
    state["on_execute"] = change
    with pytest.raises(ValueError, match="prepared source bytes"):
        run(context)


def test_pdf_preserves_original_hash_and_separately_binds_extraction(context, monkeypatch):
    repo = context[0]
    repo.docs["schwab_brokerage_statement"] = ("statement.PDF", "application/pdf", b"%PDF original")
    def extract(source, destination):
        assert source.read_bytes() == b"%PDF original"
        destination.write_bytes(b"extracted statement")
        return destination
    monkeypatch.setattr(module, "extract_pdf_text", extract)
    binding = run(context).report["binding"]
    assert binding["documents"]["schwab_brokerage_statement"]["sha256"] == sha256(b"%PDF original").hexdigest()
    for role in (MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT,
                 MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE):
        assert binding["prepared_source_sha256"][role.value] == sha256(b"extracted statement").hexdigest()


def test_historical_bytes_are_bound_and_staged_privately(context):
    name = "Account Trade History May 2025.csv"
    def inspect(**kwargs):
        assert (kwargs["historical_source_root"] / name).read_bytes() == b"history"
    context[-1]["on_execute"] = inspect
    output = run(context, historical_evidence={name: b"history"})
    assert output.report["binding"]["historical_evidence_sha256"][name] == sha256(b"history").hexdigest()


@pytest.mark.parametrize("name", ["../outside.csv", "/outside.csv", "..", "bad\\file.csv"])
def test_unsafe_historical_filenames_rejected(context, name):
    with pytest.raises(ValueError, match="safe filenames"):
        run(context, historical_evidence={name: b"history"})


def test_reference_date_mismatch_rejected(context):
    repo, storage, *_ = context
    with pytest.raises(ValueError, match="boundary date"):
        module.preview_saved_draft_for_replay(
            repo, repo.draft.id, storage, reference_closing_text=serialized(date(2025, 5, 31)))


def test_pipeline_write_attempt_rejected_and_temp_files_cleaned(context):
    state = context[-1]
    def write(**kwargs):
        kwargs["artifact_storage"].write_text("2026-09-finalized.json", "bad")
    state["on_execute"] = write
    with pytest.raises(RuntimeError, match="artifact write"):
        run(context)
    assert all(not path.exists() for path in context[2])


def test_missing_documents_fail_real_preflight_without_execution(monkeypatch):
    repo, storage = Repo(), Storage()
    repo.docs = {}
    def forbidden(*args, **kwargs):
        pytest.fail("invalid preflight reached execution")
    monkeypatch.setattr(module, "execute_monthly_import", forbidden)
    with pytest.raises(ValueError, match="preflight not ready"):
        module.preview_saved_draft_for_replay(repo, repo.draft.id, storage,
                                             reference_closing_text=serialized())


def test_incomplete_boundary_remains_blocking(context):
    context[3].boundary_reconstruction.unresolved_positions = ("META",)
    report = run(context).report
    assert any("boundary evidence" in item for item in report["blocking_findings"])
    assert not report["publication_authorized"]


@pytest.mark.parametrize("value", ["False", None, 0])
def test_ambiguous_market_choices_rejected(context, value):
    with pytest.raises(ValueError, match="explicit booleans"):
        run(context, forex_applicable=value)


def test_real_readers_preflight_preview_and_replay_preserve_external_storage():
    from campaigniq.historical_backfill_replay import audit_replay
    repo, storage = Repo(), Storage()
    storage.items["2025-05-lot-book.json"] = serialized(date(2025, 5, 31))
    tos = (
        "Account Statement test\n\n"
        "Cash Balance\n"
        "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n\n"
        "Account Trade History\n"
        "Notes,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type\n"
        ",06/02/25 10:00:00,STOCK,BUY,100,TO OPEN,IBM,,,STOCK,200,200,LMT\n\n"
        "Forex Statements\n"
        ",Date,Time,Type,Ref #,Description,Commissions & Fees,Amount,Amount(USD),Balance\n\n"
    ).encode()
    statement = (
        "June 1-30, 2025\nPositions - Equities\n"
        "IBM IBM CORP 100.0000 200.00000 20,000.00 20,000.00 0.00\n"
        "Total Equities\n"
    ).encode()
    realized = (
        "Realized Gain / Loss\n"
        "From mm/dd/yyyy 06/01/2025 To mm/dd/yyyy 06/30/2025\n"
    ).encode()
    repo.docs = {
        ROLE: ("trades.csv", "text/csv", tos),
        "schwab_brokerage_statement": ("closing.txt", "text/plain", statement),
        MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS.value:
            ("realized.txt", "text/plain", realized),
    }
    before = dict(storage.items)
    output = module.preview_saved_draft_for_replay(
        repo, repo.draft.id, storage, reference_closing_text=serialized(),
        forex_applicable=False, crypto_applicable=False)
    assert output.report["closing_reconciled"]
    assert not output.report["matches_reference"]  # Independent empty control is wrong.
    assert not output.report["publication_authorized"]
    assert storage.items == before
    replay = audit_replay(
        workspace=repo.workspace, target_period_end=date(2025, 5, 31),
        target_closing_text=serialized(date(2025, 5, 31)),
        downstream_period_end=END, periods=[output.replay_period])
    assert not replay["supplied_states_match_replay"]
    assert not replay["publication_authorized"]


def test_historical_source_mutation_rejected(context):
    name = "Account Trade History May 2025.csv"
    def change(**kwargs):
        (kwargs["historical_source_root"] / name).write_bytes(b"tampered")
    context[-1]["on_execute"] = change
    with pytest.raises(ValueError, match="historical evidence changed"):
        run(context, historical_evidence={name: b"history"})


def test_opening_allocator_adjustment_is_not_silently_accepted(context):
    context[3].opening_lot_book._next_id = 2
    output = run(context)
    assert any("adjusted opening" in finding for finding in output.report["blocking_findings"])
    assert not output.report["publication_authorized"]


def test_storage_deletion_attempt_rejected(context):
    def delete(**kwargs):
        kwargs["artifact_storage"].delete("2026-09-finalized.json")
    context[-1]["on_execute"] = delete
    with pytest.raises(RuntimeError, match="artifact deletion"):
        run(context)
