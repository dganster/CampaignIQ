from datetime import date

from campaigniq.domain.lot_book import LotBook
from campaigniq.historical_backfill_continuity import assess_continuity
from campaigniq.persistence.lot_book_store import serialize_lot_book


class ReadOnlyStorage:
    namespace = 'workspace-dennis'

    def __init__(self, items):
        self.items = items

    def list_keys(self, *, prefix='', suffix=''):
        return tuple(k for k in self.items if k.startswith(prefix) and k.endswith(suffix))

    def exists(self, key):
        return key in self.items

    def read_text(self, key):
        return self.items[key]

    def write_text(self, *args, **kwargs):
        raise AssertionError('read-only assessment attempted a write')


def test_missing_months_and_boundary_are_reported_without_publication():
    book = serialize_lot_book(period_end=date(2026, 1, 31), lot_book=LotBook())
    items = {
        '.campaigniq-monthly-publication-v1.json': '{"format":"campaigniq.monthly_publication","version":1,"first_protected_period_end":"2026-01-31"}',
        '2026-01-lot-book.json': book,
        '2026-01-finalized.json': '{}',
    }
    result = assess_continuity(ReadOnlyStorage(items), date(2025, 1, 31))
    assert result['missing_intervening_months'] == [f'2025-{m:02d}' for m in range(2, 13)]
    assert result['first_downstream_lot_book']['closing_lot_count'] == 0
    assert not result['publication_authorized']
    assert result['read_only']
    assert len(items) == 3


def test_invalid_downstream_book_fails_closed():
    items = {'2026-01-lot-book.json': '{}'}
    result = assess_continuity(ReadOnlyStorage(items), date(2025, 1, 31))
    assert result['first_downstream_lot_book'] is None
    assert any('Cannot validate' in finding for finding in result['blocking_findings'])
    assert not result['publication_authorized']
