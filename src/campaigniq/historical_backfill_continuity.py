"""Read-only historical continuity prerequisites; never certifies or publishes a backfill."""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
import re

from campaigniq.historical_backfill_impact import assess_storage, month_end
from campaigniq.persistence.lot_book_store import deserialize_lot_book


def next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def assess_continuity(storage, target: date) -> dict:
    """Inventory missing months and validate the earliest downstream lot-book envelope.

    No source-month closing book is supplied, so this deliberately cannot compare
    ending/opening positions or approve publication.
    """
    impact = assess_storage(storage, target)
    downstream = [row for row in impact['lot_book_months']
                  if row['published'] and month_end(*map(int, row['month'].split('-'))) > target]
    downstream.sort(key=lambda row: row['month'])
    gaps = []
    boundary = None
    issues = []
    if downstream:
        first = downstream[0]['month']
        y, m = next_month(target.year, target.month)
        while f'{y:04d}-{m:02d}' < first:
            gaps.append(f'{y:04d}-{m:02d}')
            y, m = next_month(y, m)
        key = f'{first}-lot-book.json'
        try:
            persisted = deserialize_lot_book(storage.read_text(key))
            expected = month_end(*map(int, first.split('-')))
            if persisted.period_end != expected:
                raise ValueError(f'period_end mismatch: {persisted.period_end} != {expected}')
            lots = [lot for instrument in persisted.lot_book.instruments()
                    for lot in persisted.lot_book.lots(instrument)]
            boundary = {'month': first, 'artifact_key': key,
                        'closing_lot_count': len(lots),
                        'note': 'This is the published month-end state, NOT its opening inventory.'}
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            issues.append(f'Cannot validate first downstream lot book {key}: {exc}')
    else:
        issues.append('No later published month was found; no downstream boundary to inspect.')
    if gaps:
        issues.append(f'{len(gaps)} intervening month(s) lack published lot books.')
    issues.append('Target month closing lot book and downstream opening inventory have not been independently compared.')
    return {
        'format': 'campaigniq.historical_backfill_continuity', 'version': 1,
        'workspace': impact['workspace'], 'target_period_end': target.isoformat(),
        'read_only': True, 'publication_authorized': False,
        'artifact_count': impact['artifact_count'],
        'baseline_fingerprint_md5': impact['baseline_fingerprint_md5'],
        'later_published_months': impact['later_published_months'],
        'missing_intervening_months': gaps, 'first_downstream_lot_book': boundary,
        'blocking_findings': issues,
        'conclusion': 'Continuity is NOT established; publication remains blocked.',
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--month', required=True, help='YYYY-MM')
    parser.add_argument('--dsn', default=None, help='Defaults to CAMPAIGNIQ_DEV_DATABASE_URL')
    args = parser.parse_args(argv)
    if args.workspace != 'workspace-dennis':
        parser.error('This development audit requires --workspace workspace-dennis')
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', args.month):
        parser.error('--month must be YYYY-MM')
    dsn = args.dsn or os.environ.get('CAMPAIGNIQ_DEV_DATABASE_URL')
    if not dsn:
        parser.error('set CAMPAIGNIQ_DEV_DATABASE_URL or provide --dsn')
    from campaigniq.persistence.postgres_artifact_storage import PostgresArtifactStorage
    storage = PostgresArtifactStorage(dsn, namespace=args.workspace)
    # No initialize(), writes, or publication calls.
    year, month = map(int, args.month.split('-'))
    print(json.dumps(assess_continuity(storage, month_end(year, month)), indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
