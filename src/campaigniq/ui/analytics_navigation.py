"""URL-backed analytics navigation and published-month range selection."""

from datetime import date
import json


FIELDS = {
    "view": "campaigniq_primary_view",
    "from": "campaigniq_range_start",
    "through": "campaigniq_range_end",
    "month": "campaigniq_campaign_month",
    "symbol": "campaigniq_campaign_symbol",
    "result": "campaigniq_campaign_result",
    "campaign": "campaigniq_campaign_detail",
    "tab": "campaigniq_performance_tab",
    "position": "campaigniq_lifecycle_symbol",
    "year": "campaigniq_range_year",
    "back": "campaigniq_return_route",
}
DEFAULTS = {
    "view": "Overview", "month": "All months", "symbol": "All symbols",
    "result": "All results", "campaign": None, "tab": "Monthly Results",
    "position": None, "back": None,
}


def route_snapshot(state):
    return {field: str(state[key]) for field, key in FIELDS.items()
            if state.get(key) is not None and state.get(key) != ""}


def _month(value):
    try:
        parsed = date.fromisoformat(str(value) + "-01")
        return value if parsed.strftime("%Y-%m") == value else None
    except (TypeError, ValueError):
        return None


def apply_route(state, route, views):
    """Restore only known UI fields; a URL never grants workspace access."""
    values = {**DEFAULTS, **{key: value for key, value in route.items() if key in FIELDS}}
    if values["view"] not in views:
        values["view"] = "Overview"
    if values["result"] not in ("All results", "Profit", "Loss", "Breakeven"):
        values["result"] = "All results"
    if values["tab"] not in ("Monthly Results", "Campaign Analysis", "Drawdown"):
        values["tab"] = "Monthly Results"
    if values["month"] != "All months" and not _month(values["month"]):
        values["month"] = "All months"
    for field in ("from", "through"):
        values[field] = _month(values.get(field))
    if not isinstance(values.get("year"), str) or not values["year"].isdigit() or len(values["year"]) != 4:
        values["year"] = None
    for field in ("campaign", "symbol", "position", "back"):
        if values.get(field) is not None and len(str(values[field])) > 8192:
            values[field] = DEFAULTS.get(field)
    for field, key in FIELDS.items():
        state[key] = values.get(field)


def restore_browser_route(ui, views):
    incoming = {field: ui.query_params.get(field) for field in FIELDS if field in ui.query_params}
    if incoming != ui.session_state.get("campaigniq_last_url_route"):
        apply_route(ui.session_state, incoming, views)
        ui.session_state["campaigniq_last_url_route"] = incoming
        # Do not reuse a table's stale selected-row offset after Back/Forward.
        ui.session_state["campaigniq_selection_generation"] = ui.session_state.get("campaigniq_selection_generation", 0) + 1
    # Keep conditionally rendered widget state when navigating to another view.
    for key in FIELDS.values():
        if key in ui.session_state:
            ui.session_state[key] = ui.session_state[key]


def publish_route(ui):
    route = route_snapshot(ui.session_state)
    existing = {field: ui.query_params.get(field) for field in FIELDS if field in ui.query_params}
    if route != existing:
        unrelated = {key: value for key, value in ui.query_params.to_dict().items()
                     if key not in FIELDS and key not in ("embed", "embed_options")}
        # One update produces one history entry; ordinary reruns produce none.
        ui.query_params.from_dict({**unrelated, **route})
    ui.session_state["campaigniq_last_url_route"] = route


def remember_return(ui):
    snapshot = route_snapshot(ui.session_state)
    ui.session_state["campaigniq_return_route"] = json.dumps(snapshot, separators=(",", ":"))


def render_return_button(ui, views):
    try:
        route = json.loads(ui.session_state.get("campaigniq_return_route") or "null")
    except (TypeError, ValueError):
        return
    if not isinstance(route, dict) or route.get("view") not in views:
        return

    def go_back():
        apply_route(ui.session_state, route, views)
        ui.session_state["campaigniq_selection_generation"] = ui.session_state.get("campaigniq_selection_generation", 0) + 1
        publish_route(ui)

    ui.button(f"← Back to {route['view']}", key="campaigniq_return_button", on_click=go_back)


def default_range(months):
    """Default to YTD for the latest published year, not the server clock."""
    latest = months[-1]
    current_year = [month for month in months if month[:4] == latest[:4]]
    return current_year[0], latest


def preset_range(months, preset, year):
    if preset == "All available data":
        return months[0], months[-1]
    candidates = [month for month in months if month[:4] == year]
    if preset.startswith("Q"):
        quarter = int(preset[1])
        candidates = [month for month in candidates
                      if (int(month[5:]) - 1) // 3 + 1 == quarter]
    return (candidates[0], candidates[-1]) if candidates else None


def render_date_range(ui, months):
    start, end = default_range(months)
    for key, fallback in (("campaigniq_range_start", start), ("campaigniq_range_end", end)):
        if ui.session_state.get(key) not in months:
            ui.session_state[key] = fallback
    if ui.session_state["campaigniq_range_start"] > ui.session_state["campaigniq_range_end"]:
        ui.session_state["campaigniq_range_end"] = ui.session_state["campaigniq_range_start"]
    ui.sidebar.markdown("#### Reporting period")
    ui.sidebar.caption("Only published months are included.")
    years = sorted({month[:4] for month in months}, reverse=True)
    if ui.session_state.get("campaigniq_range_year") not in years:
        ui.session_state["campaigniq_range_year"] = ui.session_state["campaigniq_range_end"][:4]
    ui.sidebar.selectbox("Shortcut year", years, key="campaigniq_range_year")

    def changed(field):
        if ui.session_state["campaigniq_range_start"] > ui.session_state["campaigniq_range_end"]:
            other = "campaigniq_range_end" if field == "campaigniq_range_start" else "campaigniq_range_start"
            ui.session_state[other] = ui.session_state[field]
        ui.session_state["campaigniq_campaign_month"] = "All months"
        ui.session_state["campaigniq_campaign_detail"] = None
        publish_route(ui)

    def shortcut(preset):
        selected = preset_range(months, preset, ui.session_state["campaigniq_range_year"])
        if selected:
            ui.session_state["campaigniq_range_start"], ui.session_state["campaigniq_range_end"] = selected
            changed("campaigniq_range_start")

    ui.sidebar.selectbox("From month", months, key="campaigniq_range_start",
                         format_func=lambda m: date.fromisoformat(m + "-01").strftime("%b %Y"),
                         on_change=changed, args=("campaigniq_range_start",))
    ui.sidebar.selectbox("Through month", months, key="campaigniq_range_end",
                         format_func=lambda m: date.fromisoformat(m + "-01").strftime("%b %Y"),
                         on_change=changed, args=("campaigniq_range_end",))
    columns = ui.sidebar.columns(2)
    for index, preset in enumerate(("YTD", "All available data", "Q1", "Q2", "Q3", "Q4")):
        selected = preset_range(months, preset, ui.session_state["campaigniq_range_year"])
        columns[index % 2].button(preset, key=f"campaigniq_range_{index}",
                                  on_click=shortcut, args=(preset,), disabled=selected is None)
    publish_route(ui)
    return ui.session_state["campaigniq_range_start"], ui.session_state["campaigniq_range_end"]


def filter_reporting_periods(summaries, monthly, forex, start, end):
    selected = tuple(summary for summary in summaries if start <= summary.period_start.strftime("%Y-%m") <= end)
    periods = {(summary.period_start, summary.period_end) for summary in selected}
    return selected, {key: value for key, value in monthly.items() if key in periods}, {
        key: value for key, value in forex.items() if key in periods
    }
