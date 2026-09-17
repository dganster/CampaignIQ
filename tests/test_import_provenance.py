from __future__ import annotations

import hashlib
import json
from datetime import date

from campaigniq.import_contract import MonthlyInputRole
from campaigniq.persistence.import_provenance import (
    IMPORT_PROVENANCE_FORMAT,
    IMPORT_PROVENANCE_VERSION,
    capture_monthly_input_provenance,
    save_monthly_import_provenance,
)


def test_capture_monthly_input_provenance_uses_exact_bytes_and_semantic_roles(tmp_path):
    trade = tmp_path / "anything.csv"
    statement = tmp_path / "anything-else.txt"
    trade.write_bytes(b"trade-bytes\n")
    statement.write_bytes(b"statement-bytes\r\n")

    records = capture_monthly_input_provenance(
        {
            MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT: statement,
            MonthlyInputRole.THINKORSWIM_TRADE_HISTORY: trade,
            MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE: statement,
        }
    )

    assert [item.role.value for item in records] == sorted(
        [
            MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT.value,
            MonthlyInputRole.THINKORSWIM_TRADE_HISTORY.value,
            MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE.value,
        ]
    )
    by_role = {item.role: item for item in records}
    assert by_role[MonthlyInputRole.THINKORSWIM_TRADE_HISTORY].sha256 == hashlib.sha256(
        b"trade-bytes\n"
    ).hexdigest()
    assert by_role[MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT].sha256 == hashlib.sha256(
        b"statement-bytes\r\n"
    ).hexdigest()
    assert by_role[MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT].byte_size == len(
        b"statement-bytes\r\n"
    )
    assert (
        by_role[MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE].sha256
        == by_role[MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT].sha256
    )


def test_save_monthly_import_provenance_is_deterministic_and_contains_no_source_path(tmp_path):
    source = tmp_path / "private-broker-name.csv"
    source.write_bytes(b"private source bytes")
    records = capture_monthly_input_provenance(
        {MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS: source}
    )
    target = tmp_path / "2026-08-import-provenance.json"

    save_monthly_import_provenance(
        target,
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        inputs=records,
    )

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload == {
        "format": IMPORT_PROVENANCE_FORMAT,
        "version": IMPORT_PROVENANCE_VERSION,
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
        "inputs": [
            {
                "role": MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS.value,
                "sha256": hashlib.sha256(b"private source bytes").hexdigest(),
                "byte_size": len(b"private source bytes"),
            }
        ],
    }
    assert "private-broker-name.csv" not in target.read_text(encoding="utf-8")


def test_monthly_execution_publishes_provenance_inside_finalized_artifact_set():
    source = (
        __import__("pathlib").Path("src/campaigniq/monthly_import_execution.py")
        .read_text(encoding="utf-8")
    )
    assert "capture_monthly_input_provenance(supplied_inputs)" in source
    assert "save_monthly_import_provenance(" in source
    assert "(staging_root / provenance_name).replace(state_root / provenance_name)" in source

    stage = source.index("save_monthly_import_provenance(")
    unpublish = source.index("finalized_month_marker_path(", stage)
    replace = source.index(
        "(staging_root / provenance_name).replace(state_root / provenance_name)",
        unpublish,
    )
    publish = source.index("publish_finalized_month_marker(", replace)
    assert stage < unpublish < replace < publish
