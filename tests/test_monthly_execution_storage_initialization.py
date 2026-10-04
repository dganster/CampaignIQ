from types import SimpleNamespace
from pathlib import Path
import pytest
import campaigniq.monthly_import_execution as module
from campaigniq.import_contract import MonthlyInputRole
from campaigniq.domain.lot_book import LotBook


def test_local_storage_is_initialized_before_prior_delivery_lookup(tmp_path, monkeypatch):
    from datetime import date
    from campaigniq.persistence.artifact_storage import LocalFilesystemArtifactStorage
    preflight = SimpleNamespace(ready=True,opening_lot_book=LotBook(),contract=SimpleNamespace(period_start=date(2026,6,1),period_end=date(2026,6,30)))
    inputs = {role: tmp_path/'unused.txt' for role in (MonthlyInputRole.THINKORSWIM_TRADE_HISTORY,MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS,MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT,MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT)}
    observed = []
    def verified(filename, **kwargs):
        storage = kwargs['storage']
        assert isinstance(storage, LocalFilesystemArtifactStorage)
        assert storage.root == tmp_path
        observed.append(storage)
        return ()
    monkeypatch.setattr(module, '_verified_prior_deliveries', verified)
    def reached_pipeline(self, **kwargs):
        assert kwargs['previously_applied_deliveries'] == ()
        raise RuntimeError('Reached pipeline after storage lookup')
    monkeypatch.setattr(module.PeriodImportPipeline,'run',reached_pipeline)
    with pytest.raises(RuntimeError,match='Reached pipeline'):
        module.execute_monthly_import(preflight,authoritative_state_root=str(tmp_path),supplied_inputs=inputs)
    assert len(observed) == 1
