"""Durable personal display preferences, separate from reporting filters."""
import hashlib
import json

from campaigniq.ui.table_layout import FIT_KEY


def preference_key(identity_id):
    token = hashlib.sha256(identity_id.encode("utf-8")).hexdigest()
    return f"preferences/{token}.json"


def load_fit_preference(storage, identity_id):
    key = preference_key(identity_id)
    if not storage.exists(key):
        return False
    data = json.loads(storage.read_text(key))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unsupported preferences format")
    value = data.get("fit_columns", False)
    if type(value) is not bool:
        raise ValueError("Invalid column preference")
    return value


def save_fit_preference(storage, identity_id, value):
    if type(value) is not bool:
        raise ValueError("Invalid column preference")
    storage.write_text(preference_key(identity_id), json.dumps(
        {"version": 1, "fit_columns": value}, sort_keys=True))


def render_preferences(ui, storage, access):
    # Shared-password/local sessions have no individual identity to persist.
    identity = access.identity_id if access is not None else None
    owner = (identity, access.workspace_id if access is not None else None)
    owner_key = "campaigniq_preferences_owner"
    error_key = "campaigniq_preferences_error"
    if ui.session_state.get(owner_key) != owner or FIT_KEY not in ui.session_state:
        ui.session_state[owner_key] = owner
        ui.session_state[error_key] = ""
        try:
            ui.session_state[FIT_KEY] = load_fit_preference(storage, identity) if identity else False
        except (OSError, ValueError):
            ui.session_state[FIT_KEY] = False
            ui.session_state[error_key] = "Saved preferences could not be loaded. Using the default column layout."

    def changed():
        ui.session_state[error_key] = ""
        if identity:
            try:
                save_fit_preference(storage, identity, ui.session_state[FIT_KEY])
            except (OSError, ValueError):
                ui.session_state[error_key] = "Your choice applies for this visit, but could not be saved."

    with ui.sidebar.expander("Preferences", expanded=False):
        ui.checkbox("Fit columns to contents", key=FIT_KEY, on_change=changed,
                    help="Fits every table column to its heading and displayed values. You can still resize individual columns.")
        ui.caption("Saved for your account across visits." if identity else
                   "Applies for this visit. Sign in with an individual account to save preferences.")
        if ui.session_state.get(error_key):
            ui.warning(ui.session_state[error_key])
