from datetime import date
from campaigniq.persistence.monthly_publication import (
    PROTOCOL_FILE, ensure_publication_protocol_in_storage,
    finalized_month_marker_key, is_month_published_in_storage,
    publication_cutover_from_storage, publish_finalized_month_marker_to_storage,
    unpublish_finalized_month_marker_from_storage,
)

class MemoryStorage:
    def __init__(self): self.values = {}
    def exists(self, key): return key in self.values
    def read_text(self, key): return self.values[key]
    def write_text(self, key, content): self.values[key] = content
    def delete(self, key): self.values.pop(key, None)

def test_storage_protocol_preserves_legacy_and_protects_cutover_month():
    s = MemoryStorage()
    assert ensure_publication_protocol_in_storage(s, first_period_end=date(2026,8,31)) == date(2026,8,31)
    assert publication_cutover_from_storage(s) == date(2026,8,31)
    assert is_month_published_in_storage(s, period_end=date(2026,7,31))
    assert not is_month_published_in_storage(s, period_end=date(2026,8,31))
    assert publish_finalized_month_marker_to_storage(s, period_end=date(2026,8,31)) == "2026-08-finalized.json"
    assert is_month_published_in_storage(s, period_end=date(2026,8,31))

def test_storage_protocol_cutover_is_not_moved_forward():
    s = MemoryStorage()
    ensure_publication_protocol_in_storage(s, first_period_end=date(2026,8,31))
    ensure_publication_protocol_in_storage(s, first_period_end=date(2026,9,30))
    assert publication_cutover_from_storage(s) == date(2026,8,31)

def test_storage_marker_can_unpublish_before_replacement():
    s = MemoryStorage()
    ensure_publication_protocol_in_storage(s, first_period_end=date(2026,8,31))
    publish_finalized_month_marker_to_storage(s, period_end=date(2026,8,31))
    unpublish_finalized_month_marker_from_storage(s, period_end=date(2026,8,31))
    assert not is_month_published_in_storage(s, period_end=date(2026,8,31))

def test_storage_publication_uses_logical_keys():
    s = MemoryStorage()
    ensure_publication_protocol_in_storage(s, first_period_end=date(2026,8,31))
    publish_finalized_month_marker_to_storage(s, period_end=date(2026,8,31))
    assert set(s.values) == {PROTOCOL_FILE, finalized_month_marker_key(period_end=date(2026,8,31))}
