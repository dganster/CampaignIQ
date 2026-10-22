"""Read-only bridge from persisted import documents to existing upload handlers.

The bridge never writes authoritative trading state. The caller must obtain
``workspace`` from authenticated server-side context, not browser input.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from campaigniq.import_contract import MonthlyInputRole

# Dashboard uses a single Brokerage Statement upload for both closing positions
# and assignment evidence. These keys match MONTHLY_UPLOAD_ROLES.
SUPPORTED_UPLOAD_KEYS = frozenset({
    "schwab_brokerage_statement",
    MonthlyInputRole.THINKORSWIM_TRADE_HISTORY,
    MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS,
    MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT,
    MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT,
    MonthlyInputRole.SCHWAB_NEXT_MONTH_ASSIGNMENT_EVIDENCE,
    MonthlyInputRole.SCHWAB_CRYPTO_STATEMENT,
})


@dataclass(frozen=True)
class RestoredUpload:
    """Subset of Streamlit UploadedFile consumed by existing upload preparation."""
    name: str
    media_type: str
    content: bytes

    def getvalue(self) -> bytes:
        return self.content


def restore_uploads(repository, draft_id: UUID) -> dict:
    """Restore all available inputs from a workspace-scoped draft repository.

    Missing roles are normal in partially completed drafts. Database errors,
    corrupt documents and permission failures are not suppressed.
    """
    repository.get(draft_id)  # Enforce workspace ownership even for empty drafts.
    restored = {}
    for role in SUPPORTED_UPLOAD_KEYS:
        try:
            filename, media_type, content = repository.document(draft_id, str(role.value if isinstance(role, MonthlyInputRole) else role))
        except FileNotFoundError:
            continue
        restored[role] = RestoredUpload(filename, media_type, content)
    return restored


def assert_revision_unchanged(repository, draft_id: UUID, validated_revision: int) -> None:
    """Fail closed when uploaded documents have changed since validation."""
    if repository.get(draft_id).revision != validated_revision:
        raise ValueError("Import documents changed since validation; validate again.")
