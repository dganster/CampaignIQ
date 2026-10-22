"""Read-only historical backfill impact report; never publishes artifacts."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from hashlib import md5
import json
import os
from pathlib import Path
import re

from campaigniq.historical_backfill_guard import assess_backfill
from campaigniq.persistence.monthly_publication import (
    finalized_month_marker_key,
    is_month_published_in_storage,
    publication_cutover_from_storage,
)

LOT_KEY = re.compile(r"^(\d{4})-(\d{2})-lot-book\.json$")


def month_end(year: int, month: int) -> date:
    return (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)


def assess_storage(storage, target: date) -> dict:
    """Reads artifacts without creating schemas, drafts, or publication markers."""
    if not storage.namespace:
        raise ValueError("explicit namespace required")
    keys = tuple(sorted(storage.list_keys()))
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate artifact keys")
    published = []
    months = []
    for key in keys:
        match = LOT_KEY.fullmatch(key)
        if match is None:
            continue
        year, month = map(int, match.groups())
        period_end = month_end(year, month)
        marker = finalized_month_marker_key(period_end=period_end) in keys
        is_published = is_month_published_in_storage(storage, period_end=period_end)
        months.append({"month": f"{year:04d}-{month:02d}", "lot_book": True,
                       "finalization_marker": marker, "published": is_published})
        if is_published:
            published.append(period_end)
    assessment = assess_backfill(target, published)
    # Match the previously recorded PostgreSQL fingerprint algorithm exactly.
    fingerprint_input = ','.join(f'{key}:{md5(storage.read_text(key).encode("utf-8")).hexdigest()}' for key in keys)
    cutover = publication_cutover_from_storage(storage)
    return {
        "format": "campaigniq.historical_backfill_impact", "version": 1,
        "read_only": True, "workspace": storage.namespace,
        "target_period_end": target.isoformat(), "artifact_count": len(keys),
        "baseline_fingerprint_md5": md5(fingerprint_input.encode("utf-8")).hexdigest(),
        "publication_cutover": cutover.isoformat() if cutover else None,
        "lot_book_months": months, "published_month_count": len(published),
        "later_published_months": [d.isoformat() for d in assessment.later_periods],
        "existing_period": assessment.existing_period,
        "ordinary_publication_blocked": assessment.blocked,
        "reason": assessment.message,
        "warning": "Impact inventory only; downstream position continuity has not been reconciled. No publication is authorized.",
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
    # Intentionally do not call storage.initialize(): this report is strictly read-only.
    y, m = map(int, args.month.split('-'))
    report = assess_storage(storage, month_end(y, m))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
