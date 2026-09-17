"""Locate and persist authoritative month-end CampaignIQ lot state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from campaigniq.domain.lot_book import LotBook
from campaigniq.persistence.lot_book_store import load_lot_book, save_lot_book
from campaigniq.persistence.monthly_publication import is_month_published


@dataclass(frozen=True, slots=True)
class AuthoritativeOpeningState:
    """The exact predecessor state used to open a monthly import."""

    period_end: date
    path: Path
    lot_book: LotBook


def lot_state_path(root: str | Path, *, period_end: date) -> Path:
    """Return the deterministic artifact path for one authoritative month-end."""
    return Path(root) / f"{period_end:%Y-%m}-lot-book.json"


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
