"""Durable personal display preferences, separate from reporting filters."""
import hashlib
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from campaigniq.ui.access_history_view import resolve_viewer_timezone

from campaigniq.ui.table_layout import FIT_KEY


def preference_key(identity_id):
    token = hashlib.sha256(identity_id.encode("utf-8")).hexdigest()
    return f"preferences/{token}.json"


TIMEZONE_KEY = "campaigniq_preferred_timezone"
PERIOD_KEY = "campaigniq_default_reporting_period"
AUTO_ZONE = "Automatic (browser)"
PERIODS = ("YTD", "All available data", "Q1", "Q2", "Q3", "Q4")
DEFAULT_PREFERENCES = {"fit_columns": False, "timezone": AUTO_ZONE, "reporting_period": "YTD"}


def validate_preferences(data):
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unsupported preferences format")
    values = {name: data.get(name, default) for name, default in DEFAULT_PREFERENCES.items()}
    if type(values["fit_columns"]) is not bool:
        raise ValueError("Invalid column preference")
    zone = values["timezone"]
    if not isinstance(zone, str):
        raise ValueError("Invalid time zone")
    if zone != AUTO_ZONE:
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Invalid time zone") from exc
    if values["reporting_period"] not in PERIODS:
        raise ValueError("Invalid reporting period")
    return values


def load_preferences(storage, identity_id):
    key = preference_key(identity_id)
    if not storage.exists(key):
        return dict(DEFAULT_PREFERENCES)
    return validate_preferences(json.loads(storage.read_text(key)))


def save_preferences(storage, identity_id, values):
    data = {**values, "version": 1}
    validate_preferences(data)
    storage.write_text(preference_key(identity_id), json.dumps(data, sort_keys=True))


def load_fit_preference(storage, identity_id):
    return load_preferences(storage, identity_id)["fit_columns"]


def save_fit_preference(storage, identity_id, value):
    values = load_preferences(storage, identity_id)
    values["fit_columns"] = value
    save_preferences(storage, identity_id, values)


def preferred_timezone(state, browser_zone):
    selected = state.get(TIMEZONE_KEY, AUTO_ZONE)
    return resolve_viewer_timezone(browser_zone if selected == AUTO_ZONE else selected)


def render_preferences(ui, storage, access):
    # Shared-password/local sessions have no individual identity to persist.
    identity = access.identity_id if access is not None else None
    owner = (identity, access.workspace_id if access is not None else None)
    owner_key = "campaigniq_preferences_owner"
    error_key = "campaigniq_preferences_error"
    if ui.session_state.get(owner_key) != owner or any(key not in ui.session_state for key in (FIT_KEY, TIMEZONE_KEY, PERIOD_KEY)):
        ui.session_state[owner_key] = owner
        ui.session_state[error_key] = ""
        try:
            values = load_preferences(storage, identity) if identity else dict(DEFAULT_PREFERENCES)
            for key, name in ((FIT_KEY, "fit_columns"), (TIMEZONE_KEY, "timezone"), (PERIOD_KEY, "reporting_period")):
                ui.session_state[key] = values[name]
        except (OSError, ValueError):
            for key, name in ((FIT_KEY, "fit_columns"), (TIMEZONE_KEY, "timezone"), (PERIOD_KEY, "reporting_period")):
                ui.session_state[key] = DEFAULT_PREFERENCES[name]
            ui.session_state[error_key] = "Saved preferences could not be loaded. Using the default preferences."

    def changed():
        ui.session_state[error_key] = ""
        if identity:
            try:
                save_preferences(storage, identity, {
                    "fit_columns": ui.session_state[FIT_KEY],
                    "timezone": ui.session_state[TIMEZONE_KEY],
                    "reporting_period": ui.session_state[PERIOD_KEY],
                })
            except (OSError, ValueError):
                ui.session_state[error_key] = "Your choice applies for this visit, but could not be saved."

    with ui.sidebar.expander("Preferences", expanded=False):
        ui.checkbox("Fit columns to contents", key=FIT_KEY, on_change=changed,
                    help="Fits every table column to its heading and displayed values. You can still resize individual columns.")
        zones = (AUTO_ZONE, *sorted(available_timezones()))
        ui.selectbox("Time zone", zones, key=TIMEZONE_KEY, on_change=changed,
                     help="Automatic uses your browser’s time zone. An override controls displayed visit timestamps; broker trade dates and monthly reporting dates stay as recorded.")
        zone, fallback = preferred_timezone(ui.session_state, getattr(getattr(ui, "context", None), "timezone", None))
        ui.caption(f"Display time zone: {zone.key}." + (" Browser time zone unavailable." if fallback else ""))
        ui.selectbox("Default reporting period", PERIODS, key=PERIOD_KEY, on_change=changed,
                     help="Used when no reporting dates are selected. Quarters use the latest published year; an unavailable quarter falls back to YTD. Changing this preference does not change the current report.")
        ui.caption("Saved for your account across visits." if identity else
                   "Applies for this visit. Sign in with an individual account to save preferences.")
        if ui.session_state.get(error_key):
            ui.warning(ui.session_state[error_key])
