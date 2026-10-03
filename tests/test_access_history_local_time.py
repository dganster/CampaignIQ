from copy import deepcopy
from pathlib import Path

import pytest

from campaigniq.ui.access_history_view import local_access_rows, resolve_viewer_timezone


@pytest.mark.parametrize("zone,stamp,expected", [
    ("America/Denver", "2026-10-03T12:35:03.539547+00:00", "Oct 3, 2026, 6:35:03 AM MDT"),
    ("America/Detroit", "2026-10-03T12:35:03.539547+00:00", "Oct 3, 2026, 8:35:03 AM EDT"),
    ("America/Denver", "2026-01-03T12:35:03+00:00", "Jan 3, 2026, 5:35:03 AM MST"),
    ("America/Denver", "2026-10-03T01:00:00Z", "Oct 2, 2026, 7:00:00 PM MDT"),
    ("Asia/Kolkata", "2026-10-03T12:35:03+00:00", "Oct 3, 2026, 6:05:03 PM IST"),
    ("America/Denver", "2026-11-01T07:30:00Z", "Nov 1, 2026, 1:30:00 AM MDT"),
    ("America/Denver", "2026-11-01T08:30:00Z", "Nov 1, 2026, 1:30:00 AM MST"),
])
def test_local_visit_time(zone, stamp, expected):
    tz, fallback = resolve_viewer_timezone(zone)
    assert not fallback
    assert local_access_rows([{"Visited (UTC)": stamp}], tz) == [{"Visited": expected}]


@pytest.mark.parametrize("name", [None, "", "Invalid/Zone", "../Denver", 123])
def test_explicit_utc_fallback(name):
    zone, fallback = resolve_viewer_timezone(name)
    assert fallback and zone.key == "UTC"


def test_preserves_order_and_identity_without_changing_stored_rows():
    rows = [
        {"Visited (UTC)": "2026-10-03T01:00:00Z", "Identity": "a", "User": "A"},
        {"Visited (UTC)": "2026-10-02T23:00:00Z", "Identity": "b", "User": "B"},
        {"Visited (UTC)": "2026-10-02T22:00:00Z", "Identity": "a", "User": "A"},
    ]
    original = deepcopy(rows)
    output = local_access_rows(rows, resolve_viewer_timezone("America/Denver")[0])
    assert rows == original
    assert [row["Identity"] for row in output] == ["a", "b", "a"]
    assert all("Visited (UTC)" not in row for row in output)


@pytest.mark.parametrize("stamp", [None, "broken", "2026-10-03T12:00:00"])
def test_bad_or_naive_timestamp_does_not_drop_visit(stamp):
    result = local_access_rows([{"Visited (UTC)": stamp, "Identity": "a"}], resolve_viewer_timezone("UTC")[0])
    assert result == [{"Visited": "Unavailable", "Identity": "a"}]


def test_dashboard_uses_browser_zone_for_both_history_tables():
    source = (Path(__file__).parents[1] / "src/campaigniq/ui/dashboard.py").read_text()
    block = source.split('if view == "Access History":', 1)[1].split('if view == "Crypto":', 1)[0]
    assert 'getattr(getattr(st, "context", None), "timezone", None)' in block
    assert "pd.DataFrame(local_access_rows(access_rows, viewer_zone))" in block
    assert 'access_df.drop_duplicates(subset=["Identity"])' in block
    assert "access_df.head(500)" in block
