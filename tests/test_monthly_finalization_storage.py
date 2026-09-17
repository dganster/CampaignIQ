from datetime import date, datetime
from pathlib import Path
import pytest
import campaigniq.monthly_import_execution as execution_module
from campaigniq.closing_inventory_reconciliation import ClosingInventoryReconciliation
from campaigniq.import_pipeline import PeriodImportPipeline
from campaigniq.import_preflight import prepare_monthly_import
from campaigniq.persistence.authoritative_lot_state import save_authoritative_lot_state
import tests.test_monthly_import_execution as existing

DATA=Path("tests/data")

class RecordingStorage:
    def __init__(self):
        self.values={}
        self.events=[]
        self.fail_key=None
    def exists(self,key): return key in self.values
    def read_text(self,key): return self.values[key]
    def write_text(self,key,content):
        self.events.append(("write",key))
        if key==self.fail_key: raise OSError("simulated storage payload write failure")
        self.values[key]=content
    def delete(self,key):
        self.events.append(("delete",key))
        self.values.pop(key,None)

def ready(tmp_path,monkeypatch):
    july=PeriodImportPipeline().run(
        period_start=date(2026,7,1),period_end=date(2026,7,31),
        thinkorswim_trade_history=DATA/"thinkorswim"/"Account Trade History July 2026.csv",
        opening_snapshot=DATA/"schwab"/"june_positions.txt",
        opening_snapshot_at=datetime(2026,6,30,23,59,59),
        assignment_lines=(DATA/"schwab"/"july_assignments.txt").read_text().splitlines(),
    )
    save_authoritative_lot_state(tmp_path,period_end=date(2026,7,31),lot_book=july.ending_lot_book)
    inputs=existing._august_inputs(tmp_path)
    preflight=prepare_monthly_import(2026,8,authoritative_state_root=tmp_path,supplied_inputs=inputs)
    assert preflight.ready
    monkeypatch.setattr(execution_module,"reconcile_closing_inventory",lambda **_: ClosingInventoryReconciliation(()))
    return preflight,inputs

def test_storage_finalization_writes_marker_last(tmp_path,monkeypatch):
    preflight,inputs=ready(tmp_path,monkeypatch)
    storage=RecordingStorage()
    outcome=execution_module.execute_monthly_import(
        preflight,authoritative_state_root=tmp_path,supplied_inputs=inputs,artifact_storage=storage)
    assert outcome.finalized
    assert storage.events[-1]==("write","2026-08-finalized.json")
    delete_index=storage.events.index(("delete","2026-08-finalized.json"))
    for key in ("2026-08-realized-attributions.json","2026-08-forex-settlement-attributions.json",
                "2026-08-lot-book.json","2026-08-import-provenance.json"):
        i=storage.events.index(("write",key))
        assert delete_index < i < len(storage.events)-1

def test_storage_payload_failure_leaves_month_unpublished(tmp_path,monkeypatch):
    preflight,inputs=ready(tmp_path,monkeypatch)
    storage=RecordingStorage()
    storage.values["2026-08-finalized.json"]="old marker"
    storage.fail_key="2026-08-realized-attributions.json"
    with pytest.raises(OSError,match="simulated storage payload write failure"):
        execution_module.execute_monthly_import(
            preflight,authoritative_state_root=tmp_path,supplied_inputs=inputs,artifact_storage=storage)
    assert "2026-08-finalized.json" not in storage.values
