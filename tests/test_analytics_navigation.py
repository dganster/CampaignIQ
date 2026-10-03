from datetime import date
from types import SimpleNamespace
from pathlib import Path

import pandas as pd
import pytest

from campaigniq.ui.analytics_navigation import (
    apply_route, default_range, filter_reporting_periods, preset_range,
    publish_route, remember_return, render_return_button, restore_browser_route,
    route_snapshot,
)
from campaigniq.ui.campaign_interactions import build_month_chart


class Query(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.writes = 0

    def to_dict(self):
        return dict(self)

    def from_dict(self, value):
        self.writes += 1
        self.clear()
        self.update(value)


class UI:
    def __init__(self, query=None):
        self.query_params = Query(query or {})
        self.session_state = {}

    def button(self, label, **kwargs):
        self.button_label, self.button_options = label, kwargs


VIEWS = ("Overview", "Campaigns", "Performance", "Positions", "Data")


def test_default_ytd_uses_latest_published_year():
    assert default_range(("2025-12", "2026-01", "2026-03", "2026-09")) == ("2026-01", "2026-09")


@pytest.mark.parametrize("preset,expected", [
    ("YTD", ("2026-01", "2026-09")),
    ("Q1", ("2026-01", "2026-03")),
    ("Q2", ("2026-04", "2026-06")),
    ("Q3", ("2026-07", "2026-09")),
    ("Q4", None),
    ("All available data", ("2025-12", "2026-09")),
])
def test_quarter_and_all_data_shortcuts(preset, expected):
    months = ("2025-12", *tuple(f"2026-{month:02}" for month in range(1, 10)))
    assert preset_range(months, preset, "2026") == expected


def test_range_filters_every_input_before_analytics_and_preserves_originals():
    periods = [(date(2026, m, 1), date(2026, m, 28)) for m in range(1, 7)]
    summaries = tuple(SimpleNamespace(period_start=start, period_end=end) for start, end in periods)
    monthly = {period: (f"stock-{period[0].month}",) for period in periods}
    forex = {period: (f"fx-{period[0].month}",) for period in periods}
    selected, stocks, fx = filter_reporting_periods(summaries, monthly, forex, "2026-04", "2026-06")
    assert [summary.period_start.month for summary in selected] == [4, 5, 6]
    assert list(stocks) == list(fx) == periods[3:]
    assert len(monthly) == len(forex) == len(summaries) == 6


def test_url_restore_retains_view_range_filters_and_detail():
    query = {"view": "Campaigns", "from": "2026-04", "through": "2026-06",
             "month": "2026-04", "symbol": "CMI", "result": "Loss",
             "campaign": "2026-04/CMI:CAMP-1", "tab": "Drawdown"}
    ui = UI(query)
    restore_browser_route(ui, VIEWS)
    assert all(route_snapshot(ui.session_state)[key] == value for key, value in query.items())
    assert ui.session_state["campaigniq_range_start"] == "2026-04"
    assert ui.session_state["campaigniq_range_end"] == "2026-06"
    assert ui.session_state["campaigniq_campaign_symbol"] == "CMI"
    assert ui.session_state["campaigniq_campaign_result"] == "Loss"
    assert ui.session_state["campaigniq_performance_tab"] == "Drawdown"
    assert ui.query_params.writes == 0


def test_browser_back_and_forward_restore_without_adding_history_entries():
    ui = UI({"view": "Overview", "from": "2026-01", "through": "2026-03"})
    restore_browser_route(ui, VIEWS)
    publish_route(ui)
    overview = dict(ui.query_params)
    ui.session_state["campaigniq_primary_view"] = "Campaigns"
    ui.session_state["campaigniq_campaign_month"] = "2026-03"
    publish_route(ui)
    campaign = dict(ui.query_params)
    writes = ui.query_params.writes
    ui.query_params.clear(); ui.query_params.update(overview)
    restore_browser_route(ui, VIEWS)
    assert ui.session_state["campaigniq_primary_view"] == "Overview"
    publish_route(ui)
    ui.query_params.clear(); ui.query_params.update(campaign)
    restore_browser_route(ui, VIEWS)
    assert ui.session_state["campaigniq_primary_view"] == "Campaigns"
    assert ui.session_state["campaigniq_campaign_month"] == "2026-03"
    publish_route(ui)
    assert ui.query_params.writes == writes


def test_url_updates_are_atomic_and_unchanged_reruns_create_no_history():
    ui = UI({"unrelated": "keep"})
    restore_browser_route(ui, VIEWS)
    publish_route(ui)
    assert ui.query_params.writes == 1 and ui.query_params["unrelated"] == "keep"
    publish_route(ui)
    assert ui.query_params.writes == 1


def test_visible_back_restores_source_and_original_range():
    ui = UI({"view": "Performance", "from": "2026-04", "through": "2026-06", "tab": "Monthly Results"})
    restore_browser_route(ui, VIEWS)
    remember_return(ui)
    ui.session_state["campaigniq_primary_view"] = "Campaigns"
    ui.session_state["campaigniq_campaign_month"] = "2026-04"
    render_return_button(ui, VIEWS)
    assert ui.button_label == "← Back to Performance"
    ui.button_options["on_click"]()
    assert ui.session_state["campaigniq_primary_view"] == "Performance"
    assert ui.session_state["campaigniq_range_start"] == "2026-04"
    assert ui.session_state["campaigniq_range_end"] == "2026-06"


def test_detail_back_then_month_back_returns_through_both_levels():
    ui = UI({"view": "Overview", "from": "2026-04", "through": "2026-06"})
    restore_browser_route(ui, VIEWS)
    remember_return(ui)
    ui.session_state["campaigniq_primary_view"] = "Campaigns"
    ui.session_state["campaigniq_campaign_month"] = "2026-04"
    remember_return(ui)
    ui.session_state["campaigniq_campaign_detail"] = "2026-04/CAMP-1"
    render_return_button(ui, VIEWS)
    assert ui.button_label == "← Back to Campaigns"
    ui.button_options["on_click"]()
    assert ui.session_state["campaigniq_campaign_detail"] is None
    render_return_button(ui, VIEWS)
    ui.button_options["on_click"]()
    assert ui.session_state["campaigniq_primary_view"] == "Overview"


def test_invalid_url_cannot_select_admin_view_or_set_arbitrary_state():
    state = {}
    apply_route(state, {"view": "Access History", "from": "broken", "through": "2026-99",
                        "month": "2026-99", "result": "unknown", "tab": "unknown",
                        "campaigniq_authenticated": "True", "workspace_id": "another-user"}, VIEWS)
    assert state["campaigniq_primary_view"] == "Overview"
    assert state["campaigniq_range_start"] is None
    assert state["campaigniq_campaign_month"] == "All months"
    assert "campaigniq_authenticated" not in state and "workspace_id" not in state


def test_identical_shared_chart_is_chronological_and_clickable():
    frame = pd.DataFrame({"period_start": [date(2026, 4, 1), date(2026, 1, 1)], "realized_pnl": [-25.5, 10.0]})
    chart, months = build_month_chart(frame)
    spec = chart.to_dict(validate=True)
    assert months == ("2026-01", "2026-04")
    assert spec["mark"] == {"type": "bar", "cursor": "pointer"}
    assert spec["height"] == 320
    assert spec["encoding"]["color"]["scale"]["range"] == ["#287d59", "#c44949"]
    assert spec["params"][0]["name"] == "campaign_month"
    assert spec["params"][0]["select"]["fields"] == ["month_key"]


def test_both_views_use_shared_chart_and_range_precedes_all_analytics():
    source = (Path(__file__).parents[1] / "src/campaigniq/ui/dashboard.py").read_text()
    assert source.count("build_month_chart(df)") == 2
    assert source.index("filter_reporting_periods(\n") < source.index("multi_month_performance = summarize_multi_month_performance(summaries)")
    assert 'if view in ("Overview", "Campaigns", "Performance"):' in source


def test_quarter_shortcut_keeps_sparse_published_months_and_earlier_years():
    months = ("2025-01", "2025-03", "2026-01", "2026-07")
    assert preset_range(months, "Q1", "2025") == ("2025-01", "2025-03")
    assert preset_range(months, "Q2", "2025") is None
