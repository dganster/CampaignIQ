import json
from types import SimpleNamespace
import pytest
from campaigniq.persistence.artifact_storage import LocalFilesystemArtifactStorage
from campaigniq.ui.preferences import load_fit_preference, save_fit_preference, preference_key, render_preferences
from campaigniq.ui.table_layout import FIT_KEY


def test_saved_choice_survives_new_storage_instance(tmp_path):
    save_fit_preference(LocalFilesystemArtifactStorage(tmp_path), "dennis", True)
    assert load_fit_preference(LocalFilesystemArtifactStorage(tmp_path), "dennis") is True
    save_fit_preference(LocalFilesystemArtifactStorage(tmp_path), "dennis", False)
    assert load_fit_preference(LocalFilesystemArtifactStorage(tmp_path), "dennis") is False


def test_users_are_isolated_and_identity_is_not_in_filename(tmp_path):
    storage = LocalFilesystemArtifactStorage(tmp_path)
    save_fit_preference(storage, "issuer/user/dennis", True)
    assert load_fit_preference(storage, "andrew") is False
    assert "dennis" not in preference_key("issuer/user/dennis")


@pytest.mark.parametrize("content", ['{', '[]', '{"version":1,"fit_columns":"false"}'])
def test_invalid_saved_content_is_rejected(tmp_path, content):
    storage = LocalFilesystemArtifactStorage(tmp_path)
    storage.write_text(preference_key("dennis"), content)
    with pytest.raises(ValueError):
        load_fit_preference(storage, "dennis")


class UI:
    def __init__(self):
        self.session_state = {}
        self.sidebar = self
        self.warnings = []
    def expander(self, *args, **kwargs): return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def checkbox(self, *args, **kwargs): self.changed = kwargs["on_change"]
    def caption(self, text): pass
    def warning(self, text): self.warnings.append(text)


def test_widget_callback_persists_and_new_visit_restores(tmp_path):
    storage = LocalFilesystemArtifactStorage(tmp_path)
    access = SimpleNamespace(identity_id="dennis", workspace_id="one")
    ui = UI()
    render_preferences(ui, storage, access)
    ui.session_state[FIT_KEY] = True
    ui.changed()
    another = UI()
    render_preferences(another, storage, access)
    assert another.session_state[FIT_KEY] is True
    render_preferences(another, storage, SimpleNamespace(identity_id="andrew", workspace_id="one"))
    assert another.session_state[FIT_KEY] is False


def test_unidentified_visit_does_not_write_account_preferences(tmp_path):
    storage = LocalFilesystemArtifactStorage(tmp_path)
    ui = UI()
    render_preferences(ui, storage, None)
    ui.session_state[FIT_KEY] = True
    ui.changed()
    assert storage.list_keys() == ()


def test_load_and_save_failure_keep_dashboard_usable(tmp_path):
    class Broken:
        def exists(self, key): raise OSError("offline")
        def write_text(self, key, content): raise OSError("offline")
    ui = UI()
    render_preferences(ui, Broken(), SimpleNamespace(identity_id="dennis", workspace_id="one"))
    assert ui.session_state[FIT_KEY] is False
    assert ui.warnings
    ui.session_state[FIT_KEY] = True
    ui.changed()
    assert ui.session_state[FIT_KEY] is True
    assert "could not be saved" in ui.session_state["campaigniq_preferences_error"]
