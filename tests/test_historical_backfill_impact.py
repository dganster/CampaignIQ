import hashlib
from datetime import date
from campaigniq.historical_backfill_impact import assess_storage


class FakeStorage:
    namespace = 'workspace-dennis'

    def __init__(self, items):
        self.items = items

    def list_keys(self, *, prefix='', suffix=''):
        return tuple(k for k in self.items if k.startswith(prefix) and k.endswith(suffix))

    def exists(self, key):
        return key in self.items

    def read_text(self, key):
        return self.items[key]


def test_report_blocks_backfill_and_does_not_write():
    items = {
        '.campaigniq-monthly-publication-v1.json': '{"format":"campaigniq.monthly_publication","version":1,"first_protected_period_end":"2026-09-30"}',
        '2026-08-lot-book.json': '{}',
        '2026-08-finalized.json': '{}',
        '2026-09-lot-book.json': '{}',
        '2026-09-finalized.json': '{}',
    }
    report = assess_storage(FakeStorage(items), date(2025, 1, 31))
    assert report['ordinary_publication_blocked']
    assert report['published_month_count'] == 2
    assert len(report['later_published_months']) == 2
    assert report['artifact_count'] == len(items)
    assert report['read_only']
    assert items['2026-08-lot-book.json'] == '{}'


def test_fingerprint_matches_sql_concatenation():
    items = {'b': 'two', 'a': 'one'}
    report = assess_storage(FakeStorage(items), date(2025, 1, 31))
    raw = ','.join(k + ':' + hashlib.md5(v.encode()).hexdigest() for k, v in sorted(items.items()))
    assert report['baseline_fingerprint_md5'] == hashlib.md5(raw.encode()).hexdigest()
