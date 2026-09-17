"""Locate and persist authoritative month-end CampaignIQ lot state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from campaigniq.domain.lot_book import LotBook
from campaigniq.persistence.artifact_storage import ArtifactStorage
from campaigniq.persistence.lot_book_store import (
    load_lot_book,
    load_lot_book_from_storage,
    save_lot_book,
    save_lot_book_to_storage,
)
from campaigniq.persistence.monthly_publication import is_month_published


@dataclass(frozen=True, slots=True)
class AuthoritativeOpeningState:
    """The exact predecessor state used to open a monthly import."""

    period_end: date
    path: Path
    lot_book: LotBook


def lot_state_key(*, period_end: date) -> str:
    """Return the deterministic logical key for one authoritative month-end."""
    return f"{period_end:%Y-%m}-lot-book.json"


def lot_state_path(root: str | Path, *, period_end: date) -> Path:
    """Return the deterministic artifact path for one authoritative month-end."""
    return Path(root) / lot_state_key(period_end=period_end)


def save_authoritative_lot_state_to_storage(
    storage: ArtifactStorage,
    *,
    period_end: date,
    lot_book: LotBook,
) -> str:
    """Persist month-end lot state through the provider-neutral storage boundary."""
    key = lot_state_key(period_end=period_end)
    save_lot_book_to_storage(
        storage,
        key,
        period_end=period_end,
        lot_book=lot_book,
    )
    return key


def load_preceding_authoritative_state_from_storage(
    storage: ArtifactStorage,
    *,
    period_start: date,
) -> tuple[date, str, LotBook] | None:
    """Load only the exact predecessor artifact from storage.

    Publication visibility is intentionally not decided here; Slice 6 migrates
    the publication protocol itself to the storage boundary.
    """
    expected_period_end = period_start - date.resolution
    key = lot_state_key(period_end=expected_period_end)
    if not storage.exists(key):
        return None

    persisted = load_lot_book_from_storage(storage, key)
    if persisted.period_end != expected_period_end:
        raise ValueError(
            "Authoritative lot-state artifact period mismatch: "
            f"expected {expected_period_end}, found {persisted.period_end}."
        )
    return persisted.period_end, key, persisted.lot_book


def save_authoritative_lot_state(
    root: str | Path,
    *,
    period_end: date,
    lot_book: LotBook,
) -> Path:
    """Persist a finalized month-end lot book at its canonical path."""
    path = lot_state_path(root, period_end=period_end)
    save_lot_book(path, period_end=period_end, lot_book=lot_book)
    return path


def load_preceding_authoritative_state(
    root: str | Path,
    *,
    period_start: date,
) -> AuthoritativeOpeningState | None:
    """Load the exact preceding calendar month-end, if it is available.

    Discovery is intentionally exact: a September import may use August 31
    state, but must not silently fall back to July or any other older state.
    """
    expected_period_end = period_start - date.resolution
    path = lot_state_path(root, period_end=expected_period_end)

    if not path.is_file():
        return None
    if not is_month_published(root, period_end=expected_period_end):
        return None

    persisted = load_lot_book(path)
    if persisted.period_end != expected_period_end:
        raise ValueError(
            "Authoritative lot-state artifact period mismatch: "
            f"expected {expected_period_end}, found {persisted.period_end}."
        )

    return AuthoritativeOpeningState(
        period_end=persisted.period_end,
        path=path,
        lot_book=persisted.lot_book,
    )
