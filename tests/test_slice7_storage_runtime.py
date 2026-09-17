from datetime import date

from campaigniq.domain.lot_book import LotBook
from campaigniq.import_preflight import prepare_monthly_import
from campaigniq.persistence.authoritative_lot_state import save_authoritative_lot_state_to_storage
from campaigniq.persistence.monthly_publication import (
    ensure_publication_protocol_in_storage,
    publish_finalized_month_marker_to_storage,
)
from campaigniq.persistence.persisted_multi_month_analytics import (
    load_persisted_monthly_campaign_attributions_from_storage,
)
from campaigniq.persistence.realized_attribution_store import save_realized_attributions_to_storage


class MemoryStorage:
    def __init__(self):
        self.data = {}
    def exists(self, key): return key in self.data
    def read_text(self, key): return self.data[key]
    def write_text(self, key, content): self.data[key] = content
    def delete(self, key): self.data.pop(key, None)
    def list_keys(self, *, prefix="", suffix=""):
        return tuple(sorted(k for k in self.data if k.startswith(prefix) and k.endswith(suffix)))


def test_storage_analytics_loader_reads_logical_keys():
    storage = MemoryStorage()
    save_realized_attributions_to_storage(
        storage, "2026-08-realized-attributions.json",
        period_start=date(2026, 8, 1), period_end=date(2026, 8, 31), attributions=(),
    )
    realized, forex = load_persisted_monthly_campaign_attributions_from_storage(
        storage, realized_keys=("2026-08-realized-attributions.json",), forex_keys=(),
    )
    assert (date(2026, 8, 1), date(2026, 8, 31)) in realized
    assert forex == {}


def test_storage_preflight_respects_publication_marker(tmp_path):
    storage = MemoryStorage()
    ensure_publication_protocol_in_storage(storage, first_period_end=date(2026, 8, 31))
    save_authoritative_lot_state_to_storage(
        storage, period_end=date(2026, 8, 31), lot_book=LotBook(),
    )
    preflight = prepare_monthly_import(
        2026, 9, authoritative_state_root=tmp_path, supplied_inputs={}, artifact_storage=storage,
    )
    assert preflight.opening_state is None
    publish_finalized_month_marker_to_storage(storage, period_end=date(2026, 8, 31))
    preflight = prepare_monthly_import(
        2026, 9, authoritative_state_root=tmp_path, supplied_inputs={}, artifact_storage=storage,
    )
    assert preflight.opening_state is not None
    assert preflight.opening_state.period_end == date(2026, 8, 31)
