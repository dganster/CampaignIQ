from pathlib import Path

from campaigniq.analytics.lifecycle_analytics import (
    summarize_lifecycle_history,
)
from campaigniq.persistence.lifecycle_history import (
    load_lifecycle_history_from_storage,
)
from campaigniq.persistence.artifact_storage import (
    LocalFilesystemArtifactStorage,
)
from campaigniq.ui.dashboard import lifecycle_timeline_rows


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = PROJECT_ROOT / ".campaigniq" / "authoritative_state"


def test_lifecycle_timeline_rows_describe_authoritative_nflx_history() -> None:
    storage = LocalFilesystemArtifactStorage(STATE_ROOT)
    history = load_lifecycle_history_from_storage(storage)
    analytics = summarize_lifecycle_history(history)

    nflx = next(
        summary
        for summary in analytics.symbols
        if summary.symbol == "NFLX"
    )

    rows = lifecycle_timeline_rows(nflx)

    assert len(rows) == 6

    assert [row["Transition"] for row in rows] == [
        "ROLL",
        "ROLL",
        "ROLL",
        "ROLL",
        "ROLL",
        "EXIT",
    ]

    assert rows[0]["Details"] == (
        "Feb 20, 2026 $86 Call → Feb 20, 2026 $82 Call"
    )
    assert str(rows[0]["Qty"]) == "50"

    assert rows[1]["Details"] == (
        "Feb 20, 2026 $82 Call → Feb 20, 2026 $78 Call"
    )

    assert rows[2]["Details"] == (
        "Feb 20, 2026 $78 Call → Mar 20, 2026 $74 Call"
    )

    assert rows[3]["Details"] == (
        "Mar 20, 2026 $74 Call → Apr 17, 2026 $74 Call"
    )

    assert rows[4]["Details"] == (
        "Apr 17, 2026 $74 Call → May 15, 2026 $74 Call"
    )

    assert rows[5]["Details"] == (
        "5,000 NFLX shares + "
        "50 short May 15, 2026 $74 Call contracts "
        "→ zero exposure"
    )
    assert rows[5]["Qty"] == ""
