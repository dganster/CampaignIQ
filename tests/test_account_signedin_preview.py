import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from campaigniq import account_signedin_preview as app
from campaigniq.account_legacy_access import LegacyAccountChoice


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    return tmp_path


def config_file(home):
    folder = home / 'CampaignIQ-Records/Development-Previews/test'
    folder.mkdir(parents=True)
    path = folder / 'preview-config.json'
    value = dict(format='campaigniq.signedin_preview', version=1, registry_id=str(uuid4()),
        issuer='https://accounts.example.test', authorization={'a':'workspace-a'}, readonly=True)
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


def test_config_private_exact_and_not_demo(home):
    path = config_file(home)
    assert app.load_config(path)['readonly']
    path.chmod(0o644)
    with pytest.raises(ValueError): app.load_config(path)
    path.chmod(0o600)
    value = json.loads(path.read_text())
    value['format'] = 'campaigniq.admin_preview'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError): app.load_config(path)


def test_config_symlink_or_outside_saved_root_rejected(home):
    path = config_file(home)
    link = path.with_name('linked.json')
    link.symlink_to(path)
    with pytest.raises(ValueError): app.load_config(link)
    outside = home / 'outside.json'
    outside.write_bytes(path.read_bytes())
    outside.chmod(0o600)
    with pytest.raises(ValueError): app.load_config(outside)


@pytest.mark.parametrize('raw', ['{}', '{"a":"workspace-a","a":"workspace-b"}',
    '{" a":"workspace-a"}', '{"a":"workspace-a","b":"workspace-a"}'])
def test_prepare_rejects_bad_mapping_before_any_database_or_file_write(home, monkeypatch, raw):
    monkeypatch.setenv('CAMPAIGNIQ_WORKSPACE_AUTHORIZATION', raw)
    store = Mock()
    monkeypatch.setattr(app, 'local_store', store)
    with pytest.raises((ValueError, TypeError)):
        app.prepare('https://accounts.example.test', 'a')
    store.assert_not_called()
    assert not list(home.iterdir())


def test_prepare_only_bootstraps_explicit_local_metadata_and_saves_receipt_first(home, monkeypatch):
    monkeypatch.setenv('CAMPAIGNIQ_WORKSPACE_AUTHORIZATION', '{"a":"workspace-a","b":"workspace-b"}')
    store = Mock()
    def initialize():
        assert len(list(home.rglob('preview-config.json'))) == 1
    store.initialize.side_effect = initialize
    monkeypatch.setattr(app, 'local_store', lambda _: store)
    path = app.prepare('https://accounts.example.test', 'a')
    config = app.load_config(path)
    assert config['authorization'] == {'a':'workspace-a','b':'workspace-b'}
    initial = store.bootstrap.call_args.args[0]
    assert [a.storage_scope for a in initial.accounts] == ['workspace-a','workspace-b']
    assert [call[0] for call in store.method_calls] == ['initialize', 'bootstrap']
    assert 'password' not in path.read_text() and 'dsn' not in path.read_text()


@pytest.mark.parametrize('env', ['RENDER', 'RENDER_SERVICE_ID'])
def test_preparation_rejects_render_before_saving(home, monkeypatch, env):
    monkeypatch.setenv('CAMPAIGNIQ_WORKSPACE_AUTHORIZATION', '{"a":"workspace-a"}')
    monkeypatch.setenv(env, 'true')
    with pytest.raises(ValueError): app.prepare('https://accounts.example.test', 'a')
    assert not list(home.iterdir())


class UI:
    def __init__(self, logged_in, pressed=()):
        self.user = SimpleNamespace(to_dict=lambda: dict(is_logged_in=logged_in))
        self.pressed = pressed
        self.messages = []
    def title(self, value): self.messages.append(value)
    def info(self, value): self.messages.append(value)
    def caption(self, value): self.messages.append(value)
    def success(self, value): self.messages.append(value)
    def error(self, value): self.messages.append(value)
    def button(self, label): return label in self.pressed
    def selectbox(self, label, choices, **kwargs): return choices[0]
    def dataframe(self, rows, **kwargs): self.messages.append(rows)
    def login(self): self.messages.append('OIDC login invoked')
    def logout(self): self.messages.append('OIDC logout invoked')


def test_logged_out_ui_never_reads_directory_or_financial_rows():
    service = Mock()
    ui = UI(False, ('Sign in',))
    app.render(ui, service)
    assert 'OIDC login invoked' in ui.messages
    assert not service.mock_calls


def test_signed_out_ui_does_not_continue_to_data():
    service = Mock()
    ui = UI(True, ('Sign out',))
    app.render(ui, service)
    assert 'OIDC logout invoked' in ui.messages
    assert not service.mock_calls


def test_signedin_ui_routes_server_session_and_selected_account_to_fresh_reader():
    service = Mock()
    service.choices.return_value = (LegacyAccountChoice('account-id', 'Existing account', False),)
    service.inventory.return_value = SimpleNamespace(artifact_count=3, active_drafts=1,
        abandoned_drafts=2, document_versions=5, original_bytes=20)
    ui = UI(True, ('Verify existing saved data',))
    app.render(ui, service)
    service.choices.assert_called_once_with(ui.user)
    service.inventory.assert_called_once_with(ui.user, 'account-id')
    assert any('No data was copied' in v for v in ui.messages if isinstance(v, str))
    assert not any('Publish' in v for v in ui.messages if isinstance(v, str))


def test_unauthorized_ui_does_not_expose_directory_error_or_inventory():
    service = Mock()
    service.choices.side_effect = PermissionError('private identity and account details')
    ui = UI(True)
    app.render(ui, service)
    service.inventory.assert_not_called()
    assert 'private identity' not in str(ui.messages)


def test_real_streamlit_logged_out_screen_has_no_demo_selector():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string('''
import streamlit as st
from unittest.mock import Mock
from campaigniq.account_signedin_preview import render
render(st, Mock())
''').run(timeout=15)
    assert not at.exception
    assert [b.label for b in at.button] == ['Sign in']
    assert not at.selectbox


def test_signedin_comparison_receives_same_server_session_and_service(monkeypatch):
    from campaigniq.ui import account_signedin_inputs_view,account_signedin_comparison_view
    inputs=Mock();legacy=Mock();legacy.choices.return_value=()
    render_inputs=Mock();render_comparison=Mock()
    monkeypatch.setattr(account_signedin_inputs_view,'render',render_inputs)
    monkeypatch.setattr(account_signedin_comparison_view,'render',render_comparison)
    ui=UI(False)
    app.render(ui,legacy,inputs=inputs)
    render_inputs.assert_not_called();render_comparison.assert_not_called()
    ui=UI(True)
    app.render(ui,legacy,inputs=inputs)
    render_inputs.assert_called_once_with(ui,inputs,ui.user)
    render_comparison.assert_called_once_with(ui,inputs,ui.user)
