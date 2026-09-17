"""Publication boundary for finalized monthly CampaignIQ artifacts."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile


PROTOCOL_FILE = ".campaigniq-monthly-publication-v1.json"


def finalized_month_marker_path(root: str | Path, *, period_end: date) -> Path:
    return Path(root) / f"{period_end:%Y-%m}-finalized.json"


def ensure_publication_protocol(root: str | Path, *, first_period_end: date) -> date:
    """Enable marker-based publication before publishing protected artifacts.

    Months before the recorded cutover retain legacy discovery semantics. The
    cutover itself is atomically published before any protected payload rename.
    """
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    protocol_path = root_path / PROTOCOL_FILE
    if protocol_path.is_file():
        return publication_cutover(root_path) or first_period_end

    payload = {
        "format": "campaigniq.monthly_publication",
        "version": 1,
        "first_protected_period_end": first_period_end.isoformat(),
    }
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=root_path,
        prefix=".campaigniq-publication-protocol-",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    try:
        temp_path.replace(protocol_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return first_period_end


def publication_cutover(root: str | Path) -> date | None:
    protocol_path = Path(root) / PROTOCOL_FILE
    if not protocol_path.is_file():
        return None
    payload = json.loads(protocol_path.read_text(encoding="utf-8"))
    if (
        payload.get("format") != "campaigniq.monthly_publication"
        or payload.get("version") != 1
    ):
        raise ValueError("Unsupported CampaignIQ monthly publication protocol.")
    return date.fromisoformat(payload["first_protected_period_end"])


def month_requires_finalization_marker(
    root: str | Path,
    *,
    period_end: date,
) -> bool:
    cutover = publication_cutover(root)
    return cutover is not None and period_end >= cutover


def is_month_published(root: str | Path, *, period_end: date) -> bool:
    if not month_requires_finalization_marker(root, period_end=period_end):
        return True
    return finalized_month_marker_path(root, period_end=period_end).is_file()


def publish_finalized_month_marker(root: str | Path, *, period_end: date) -> Path:
    root_path = Path(root)
    marker = finalized_month_marker_path(root_path, period_end=period_end)
    payload = {
        "format": "campaigniq.finalized_month",
        "version": 1,
        "period_end": period_end.isoformat(),
    }
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=root_path,
        prefix=".campaigniq-finalized-month-",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    try:
        temp_path.replace(marker)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return marker
