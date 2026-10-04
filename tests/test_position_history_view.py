from datetime import date, datetime
from decimal import Decimal as D
import json
from types import SimpleNamespace

from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.artifact_storage import LocalFilesystemArtifactStorage
from campaigniq.persistence.lot_book_store import save_lot_book_to_storage
from campaigniq.persistence.monthly_publication import ensure_publication_protocol_in_storage, publish_finalized_month_marker_to_storage
from campaigniq.persistence.position_journal import serialize_position_journal
from campaigniq.ui.position_history_view import campaign_entries, history_rows, load_position_evidence
from tests.test_position_journal import imported, trade, Campaign, Side, Effect, START, END


def attribution(cid, *, day=20, symbol="ABC", lid="LOT-000001"):
    record = RealizedGainLossRecord(instrument=Instrument(symbol), quantity=D(1), proceeds=D(110), cost_basis=D(100),
        gain_loss=D(10), term="SHORT", closed_date=date(2026, 9, day), closing_price=D(110), basis_method="test")
    return RealizedAttribution(record=record, allocations=(LotAllocation(lid, D(1), D(100), "test", cid),))


def journal(cid, *, symbol="ABC"):
    result = imported([Campaign(cid, (trade(symbol, Side.BUY, Effect.OPEN, "1", 1), trade(symbol, Side.SELL, Effect.CLOSE, "1", 20)))])
    text = serialize_position_journal(result, period_start=START, period_end=END)
    return [dict(row, period_start="2026-09-01", source="Published position journal") for row in json.loads(text)["entries"]]


def test_campaign_selection_excludes_other_campaigns_and_same_id_other_underlying():
    entries = journal("2026-09:ABC:CAMP-000001") + journal("2026-09:ABC:CAMP-000002") + journal("2026-09:ABC:CAMP-000001", symbol="XYZ")
    chosen = campaign_entries(entries, [attribution("2026-09/2026-09:ABC:CAMP-000001")])
    assert len(chosen) == 2
    assert all(row["instrument"]["symbol"] == "ABC" and row["campaign_id"].endswith("000001") for row in chosen)


def test_legacy_id_and_reused_lot_number_do_not_link_other_months():
    entries = journal("CAMP-000001")
    august = [dict(row, period_start="2026-08-01", occurred_at=row["occurred_at"].replace("2026-09", "2026-08")) for row in journal("CAMP-000001")]
    chosen = campaign_entries(entries + august, [attribution("2026-09/CAMP-000001")])
    assert chosen == entries


def test_unavailable_provenance_shows_only_unique_close_without_guessing_opening():
    entries = [dict(row, campaign_id=None, position_after=None, lots=[]) for row in journal("unknown")]
    chosen = campaign_entries(entries, [attribution("2026-09/old")])
    assert len(chosen) == 1 and chosen[0]["action"] == "Close / reduce"
    assert chosen[0]["position_after"] is None
    assert campaign_entries(entries + entries[-1:], [attribution("2026-09/old")]) == []


def test_published_journals_respect_visibility_and_workspace_boundaries(tmp_path):
    storage = LocalFilesystemArtifactStorage(tmp_path / "a")
    result = imported([Campaign("one", (trade("ABC", Side.BUY, Effect.OPEN, "1", 1),))])
    storage.write_text("2026-09-position-journal.json", serialize_position_journal(result, period_start=START, period_end=END))
    ensure_publication_protocol_in_storage(storage, first_period_end=END)
    monthly = {(START, END): ()}; lifecycle = SimpleNamespace(transitions=())
    assert load_position_evidence(storage, tmp_path, monthly, lifecycle) == ([], [])
    publish_finalized_month_marker_to_storage(storage, period_end=END)
    entries, gaps = load_position_evidence(storage, tmp_path, monthly, lifecycle)
    assert len(entries) == 1 and gaps == []
    assert load_position_evidence(LocalFilesystemArtifactStorage(tmp_path / "b"), tmp_path / "b", {}, lifecycle) == ([], [])


def test_corrupt_published_journal_reports_gap_without_raw_replacement(tmp_path):
    storage = LocalFilesystemArtifactStorage(tmp_path)
    storage.write_text("2026-09-position-journal.json", "{}"); monthly = {(START, END): ()}
    entries, gaps = load_position_evidence(storage, tmp_path, monthly, SimpleNamespace(transitions=()))
    assert entries == [] and "could not be read" in gaps[0]


def test_source_replay_backfills_history_only_when_published_lot_allocations_agree(tmp_path):
    from campaigniq.import_pipeline import PeriodImportPipeline
    export = '''Account Statement for synthetic account since 8/31/26 through 9/30/26

Cash Balance
DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE

Account Trade History
,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type
,9/1/26 10:00:00,STOCK,BUY,+1,TO OPEN,ABC,,,STOCK,100,100,LMT
,9/20/26 10:00:00,STOCK,SELL,-1,TO CLOSE,ABC,,,STOCK,110,110,LMT

Forex Statements
Trade Date,Exec Date,Exec Time,Type,Ref #,Description,Commissions & Fees,Amount,Balance

'''
    path = tmp_path / "Account Trade History September 2026.csv"; path.write_text(export)
    result = PeriodImportPipeline().run(period_start=START, period_end=END, thinkorswim_trade_history=path, carried_opening_lot_book=LotBook(), campaign_namespace="2026-09")
    storage = LocalFilesystemArtifactStorage(tmp_path / "state")
    save_lot_book_to_storage(storage, "2026-09-lot-book.json", period_end=END, lot_book=result.ending_lot_book)
    save_lot_book_to_storage(storage, "2026-08-lot-book.json", period_end=date(2026, 8, 31), lot_book=LotBook())
    attrs = (attribution("2026-09:ABC:CAMP-000001"),)
    entries, gaps = load_position_evidence(storage, tmp_path, {(START, END): attrs}, SimpleNamespace(transitions=()))
    assert gaps == [] and [row["action"] for row in entries] == ["Open / add", "Close / reduce"]
    assert len(campaign_entries(entries, [attribution("2026-09/2026-09:ABC:CAMP-000001")])) == 2
    # Exact broker allocation mismatch invalidates campaign provenance even
    # though the underlying, close date and ending zero position still match.
    entries, gaps = load_position_evidence(storage, tmp_path, {(START, END): (attribution("different"),)}, SimpleNamespace(transitions=()))
    assert gaps and all(row["campaign_id"] is None for row in entries)


def test_underlying_rows_preserve_signed_quantities_and_known_balances():
    rows = history_rows(journal("one"), lambda i: i.symbol, include_campaign=True)
    assert rows[0]["Quantity change"] == 1 and rows[-1]["Quantity change"] == -1
    assert rows[-1]["Position after"] == 0 and rows[0]["Campaign"] == "one"


def test_carried_lot_links_original_opening_and_intervening_partial_close():
    original = journal("2026-08:ABC:CAMP-000001")
    original = [dict(row, period_start="2026-08-01", occurred_at=row["occurred_at"].replace("2026-09", "2026-08")) for row in original]
    original[0]["quantity_change"] = "2"; original[0]["position_after"] = "2"
    original[0]["lots"][0]["quantity"] = "2"; original[1]["position_after"] = "1"
    current = journal("2026-09:ABC:CAMP-000002")
    carried = dict(current[0], action="Carried position", occurred_at="2026-09-01T00:00:00",
                   quantity_change="1", price=None, opened_at=None,
                   opening_refs=[dict(lot_id="LOT-000001", opened_at=original[0]["occurred_at"], quantity="1")])
    chosen = campaign_entries(original + [carried, current[1]], [attribution("2026-09/2026-09:ABC:CAMP-000002")])
    rows = history_rows(chosen, lambda i: i.symbol)
    assert [row["Quantity change"] for row in rows] == [2, -1, -1]
    assert [row["Position after"] for row in rows] == [2, 1, 0]


def test_realized_pnl_is_attached_only_to_unique_broker_close_match():
    rows = history_rows(journal("one"), lambda i: i.symbol, realized_records=(attribution("one"),))
    assert rows[0]["Realized P&L"] is None and rows[-1]["Realized P&L"] == 10
    duplicated = journal("one") + journal("two")
    assert all(row["Realized P&L"] is None for row in history_rows(duplicated, lambda i:i.symbol, realized_records=(attribution("one"),)))


def test_replaced_archive_cannot_masquerade_as_published_source_evidence(tmp_path):
    from campaigniq.import_contract import MonthlyInputRole
    from campaigniq.persistence.import_provenance import MonthlyInputProvenance, serialize_monthly_import_provenance
    storage = LocalFilesystemArtifactStorage(tmp_path / "state")
    storage.write_text("2026-09-import-provenance.json", serialize_monthly_import_provenance(
        period_start=START, period_end=END,
        inputs=(MonthlyInputProvenance(MonthlyInputRole.THINKORSWIM_TRADE_HISTORY, "0" * 64, 10),)))
    (tmp_path / "Account Trade History September 2026.csv").write_text("different evidence")
    entries, gaps = load_position_evidence(storage, tmp_path, {(START, END): ()}, SimpleNamespace(transitions=()))
    assert not entries and gaps
