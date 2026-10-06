from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from campaigniq.persistence.artifact_storage import LocalFilesystemArtifactStorage
from campaigniq.ui.preferences import (
    load_preferences, save_preferences, preference_key, preferred_timezone,
    render_preferences, TIMEZONE_KEY, PERIOD_KEY, AUTO_ZONE,
)
from campaigniq.ui.access_history_view import local_access_rows
from campaigniq.ui.analytics_navigation import render_date_range, apply_route


class Query(dict):
    def to_dict(self): return dict(self)
    def from_dict(self, values): self.clear(); self.update(values)


class UI:
    def __init__(self):
        self.session_state = {}
        self.query_params = Query()
        self.sidebar = self
        self.context = SimpleNamespace(timezone="America/Denver")
    def expander(self, *args, **kwargs): return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def checkbox(self, *args, **kwargs): self.changed = kwargs["on_change"]
    def selectbox(self, *args, **kwargs):
        if "on_change" in kwargs: self.changed = kwargs["on_change"]
    def caption(self, text): pass
    def warning(self, text): pass
    def markdown(self, text): pass
    def columns(self, n): return [self] * n
    def button(self, *args, **kwargs): pass


def test_old_column_only_preferences_upgrade_without_losing_choice(tmp_path):
    storage = LocalFilesystemArtifactStorage(tmp_path)
    storage.write_text(preference_key("user"), '{"version":1,"fit_columns":true}')
    assert load_preferences(storage, "user") == {
        "fit_columns": True, "timezone": AUTO_ZONE, "reporting_period": "YTD"}


def test_all_preferences_saved_restored_and_isolated(tmp_path):
    storage = LocalFilesystemArtifactStorage(tmp_path)
    ui = UI()
    access = SimpleNamespace(identity_id="user", workspace_id="one")
    render_preferences(ui, storage, access)
    ui.session_state[TIMEZONE_KEY] = "America/New_York"
    ui.session_state[PERIOD_KEY] = "Q2"
    ui.changed()
    next_visit = UI()
    render_preferences(next_visit, storage, access)
    assert next_visit.session_state[TIMEZONE_KEY] == "America/New_York"
    assert next_visit.session_state[PERIOD_KEY] == "Q2"
    render_preferences(next_visit, storage, SimpleNamespace(identity_id="other", workspace_id="one"))
    assert next_visit.session_state[TIMEZONE_KEY] == AUTO_ZONE
    assert next_visit.session_state[PERIOD_KEY] == "YTD"


@pytest.mark.parametrize("browser,expected,fallback", [
    ("America/Denver", "America/Denver", False), (None, "UTC", True)])
def test_automatic_timezone(browser, expected, fallback):
    zone, missing = preferred_timezone({}, browser)
    assert (zone.key, missing) == (expected, fallback)


def test_manual_timezone_and_daylight_saving():
    zone, missing = preferred_timezone({TIMEZONE_KEY: "America/Denver"}, "Asia/Tokyo")
    assert not missing
    rows = local_access_rows([{"Visited (UTC)": "2026-07-01T12:00:00+00:00"},
                              {"Visited (UTC)": "2026-01-01T12:00:00+00:00"}], zone)
    assert "6:00:00 AM MDT" in rows[0]["Visited"]
    assert "5:00:00 AM MST" in rows[1]["Visited"]


@pytest.mark.parametrize("values", [
    {"timezone": "Not/AZone"}, {"reporting_period": "Q9"}, {"fit_columns": 1}])
def test_bad_preferences_never_written(tmp_path, values):
    storage = LocalFilesystemArtifactStorage(tmp_path)
    with pytest.raises(ValueError): save_preferences(storage, "user", values)
    assert not storage.exists(preference_key("user"))


MONTHS = ["2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07"]


@pytest.mark.parametrize("preset,expected", [
    ("YTD", ("2026-01", "2026-07")),
    ("All available data", ("2025-12", "2026-07")),
    ("Q1", ("2026-01", "2026-03")),
    ("Q2", ("2026-04", "2026-06")),
    ("Q3", ("2026-07", "2026-07")),
    ("Q4", ("2026-01", "2026-07"))])
def test_default_reporting_presets(preset, expected):
    ui = UI(); ui.session_state[PERIOD_KEY] = preset
    assert render_date_range(ui, MONTHS) == expected


def test_explicit_link_dates_and_current_report_survive_preference_change():
    ui = UI(); ui.session_state[PERIOD_KEY] = "Q2"
    apply_route(ui.session_state, {"from": "2026-02", "through": "2026-03"}, ("Overview",))
    assert render_date_range(ui, MONTHS) == ("2026-02", "2026-03")
    ui.session_state[PERIOD_KEY] = "All available data"
    assert render_date_range(ui, MONTHS) == ("2026-02", "2026-03")
