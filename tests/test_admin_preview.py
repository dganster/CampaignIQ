"""Real Streamlit form interactions using the actual policy with an in-memory store."""
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest

from campaigniq.admin_preview import load_config, require_local_mode, run_app, streamlit_command
from campaigniq.account_administration import Identity
from campaigniq.ui.account_administration_view import DEMO_ISSUER, demo_subject_prefix, validate_demo_directory


APP = '''
import streamlit as st
from campaigniq.account_administration import Identity, LegacyOwner, propose_legacy_directory
from campaigniq.ui.account_administration_view import DEMO_ISSUER, demo_subject_prefix, render_preview
REGISTRY = '00000000-0000-4000-8000-000000000001'
prefix = demo_subject_prefix(REGISTRY)
if 'directory' not in st.session_state:
    admin = Identity(DEMO_ISSUER, prefix + 'admin')
    member = Identity(DEMO_ISSUER, prefix + 'member')
    st.session_state['directory'] = propose_legacy_directory((
        LegacyOwner(admin, 'workspace-demo-admin', 'Demo administrator', 'Demo taxable account', 'Demo broker'),
        LegacyOwner(member, 'workspace-demo-member', 'Demo member', 'Demo retirement account', 'Demo broker'),
    ), admin)
class Store:
    def _snapshot(self):
        if st.session_state.get('corrupt_store', False):
            raise ValueError('Simulated integrity failure')
        return st.session_state['directory']
    def execute(self, actor_identity, revision, command, /, **arguments):
        d = self._snapshot()
        st.session_state['directory'] = getattr(d, command)(actor_identity, expected_revision=revision, **arguments)
    def authorize(self, identity, account_id, **kwargs):
        return self._snapshot().authorize(identity, account_id, **kwargs)
    def select(self, identity, account_ids):
        return self._snapshot().select(identity, account_ids)
    def activity(self, identity):
        d = self._snapshot()
        if not d.user(identity).admin:
            raise PermissionError('Administrator required')
        return d.events
render_preview(st, Store(), REGISTRY)
'''


def element(elements, label):
    return next(e for e in elements if e.label == label)


@pytest.fixture
def app():
    result = AppTest.from_string(APP, default_timeout=15).run()
    assert not result.exception
    return result


def test_admin_preview_is_explicitly_synthetic_and_shows_admin_forms(app):
    assert 'Synthetic' in app.warning[0].value
    assert element(app.button, 'Add demo user')
    assert element(app.button, 'Save access')
    assert len(app.session_state['directory'].users) == 2


def test_add_user_creates_only_synthetic_member_and_records_activity(app):
    element(app.text_input, 'New user name').input('New demo member')
    element(app.button, 'Add demo user').click().run()
    assert not app.exception
    d = app.session_state['directory']
    assert d.users[-1].name == 'New demo member' and not d.users[-1].admin
    assert d.users[-1].identity.issuer == DEMO_ISSUER
    assert d.events[-1].action == 'add_user'
    assert app.success


def test_invalid_user_form_does_not_change_directory(app):
    before = app.session_state['directory']
    element(app.button, 'Add demo user').click().run()
    assert not app.exception
    assert app.session_state['directory'] == before
    assert app.error


def test_account_form_preserves_separate_account_ids(app):
    element(app.text_input, 'New account name').input('IRA')
    element(app.text_input, 'Broker').input('Other broker')
    element(app.button, 'Add account').click().run()
    assert not app.exception
    d = app.session_state['directory']
    assert d.accounts[-1].name == 'IRA' and d.accounts[-1].broker == 'Other broker'
    assert len({a.storage_scope for a in d.accounts}) == 3


def test_sharing_view_edit_and_revoke_through_forms(app):
    for choice, expected in [('View', 'view'), ('Edit', 'edit'), ('No shared access', None)]:
        element(app.selectbox, 'Permission').select(choice)
        element(app.button, 'Save access').click().run()
        assert not app.exception
        grants = app.session_state['directory'].grants
        assert (grants[0].permission.value if grants else None) == expected


def test_member_sees_only_own_accounts_and_no_admin_forms(app):
    member_id = app.session_state['directory'].users[1].id
    app.sidebar.selectbox[0].set_value(member_id).run()
    assert not app.exception
    assert not any(b.label in {'Save user', 'Add account', 'Save access'} for b in app.button)
    choices = app.sidebar.multiselect[0].options
    assert len(choices) == 1 and 'retirement' in choices[0]
    assert any('requires an enabled administrator' in x.value for x in app.info)


def test_disabling_member_blocks_preview_account_access(app):
    member_id = app.session_state['directory'].users[1].id
    element(app.selectbox, 'User to manage').set_value(member_id).run()
    element(app.checkbox, 'User enabled').uncheck()
    element(app.button, 'Save user').click().run()
    assert not app.exception
    assert not app.session_state['directory'].users[1].enabled
    app.sidebar.selectbox[0].set_value(member_id).run()
    assert not app.exception
    assert any('disabled' in e.value for e in app.error)
    assert len(app.sidebar.multiselect) == 0


def test_last_administrator_cannot_be_demoted_from_ui(app):
    before = app.session_state['directory']
    element(app.checkbox, 'Administrator').uncheck()
    element(app.button, 'Save user').click().run()
    assert not app.exception and app.session_state['directory'] == before
    assert any('administrator' in e.value for e in app.error)


def test_archiving_and_renaming_keep_account_binding(app):
    original = app.session_state['directory'].accounts[0]
    element(app.text_input, 'Account name').input('Archived taxable account')
    element(app.checkbox, 'Account archived').check()
    element(app.button, 'Save account').click().run()
    assert not app.exception
    account = app.session_state['directory'].accounts[0]
    assert account.id == original.id and account.storage_scope == original.storage_scope
    assert account.archived and account.name == 'Archived taxable account'


def test_stale_displayed_form_revision_rejects_overwrite_and_reload_recovers(app):
    d = app.session_state['directory']
    updated = d.configure_account(d.users[0].identity, d.accounts[0].id, name='Concurrent update', expected_revision=0)
    app.session_state['directory'] = updated
    element(app.text_input, 'New account name').input('Stale addition')
    element(app.text_input, 'Broker').input('Other')
    element(app.button, 'Add account').click().run()
    assert not app.exception and app.session_state['directory'] == updated
    assert any('reload' in e.value for e in app.error)
    element(app.button, 'Reload directory').click().run()
    element(app.text_input, 'New account name').input('Fresh addition')
    element(app.text_input, 'Broker').input('Other')
    element(app.button, 'Add account').click().run()
    assert not app.exception and len(app.session_state['directory'].accounts) == 3


def test_directory_integrity_error_never_displays_admin_forms(app):
    app.session_state['corrupt_store'] = True
    app.run()
    assert not app.exception and app.error
    assert not any(b.label == 'Save user' for b in app.button)
    assert len(app.dataframe) == 0


def test_preview_cannot_display_rehearsal_or_real_provider_registry(app):
    from dataclasses import replace
    d = app.session_state['directory']
    users = (replace(d.users[0], identity=Identity('https://accounts.google.com', 'real-provider-subject')),) + d.users[1:]
    d = replace(d, users=users)
    with pytest.raises(ValueError):
        validate_demo_directory(d, '00000000-0000-4000-8000-000000000001')


@pytest.mark.parametrize('flag', [False, 'true', 1, None])
def test_explicit_local_preview_mode_required(flag, monkeypatch):
    monkeypatch.delenv('RENDER', raising=False)
    monkeypatch.delenv('RENDER_SERVICE_ID', raising=False)
    with pytest.raises(ValueError):
        require_local_mode(flag)


@pytest.mark.parametrize('name', ['RENDER', 'RENDER_SERVICE_ID'])
def test_preview_refuses_render(name, monkeypatch):
    monkeypatch.setenv(name, 'true')
    with pytest.raises(ValueError):
        require_local_mode(True)


def test_launcher_always_uses_loopback_and_browser_protections():
    command = streamlit_command()
    for value in ['--server.address=127.0.0.1', '--server.enableCORS=true', '--server.enableXsrfProtection=true']:
        assert value in command
    assert not any('0.0.0.0' in c for c in command)


def test_actual_service_dispatch_distinguishes_actor_from_new_user_identity(monkeypatch):
    from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
    for name in ('RENDER', 'RENDER_SERVICE_ID', 'PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS'):
        monkeypatch.delenv(name, raising=False)
    store = LocalPostgresAccountDirectory('dbname=campaigniq_dev', str(uuid4()), development_only=True)
    class TransactionReached(Exception):
        pass
    def connection():
        raise TransactionReached()
    monkeypatch.setattr(store, '_connection', connection)
    actor = Identity(DEMO_ISSUER, 'synthetic-admin')
    new_user = Identity(DEMO_ISSUER, 'synthetic-member')
    # A name collision used to fail before the service could dispatch add_user.
    with pytest.raises(TransactionReached):
        store.execute(actor, 0, 'add_user', identity=new_user, name='New member')


@pytest.mark.parametrize('settings', [
    {'server.address': '0.0.0.0', 'server.enableCORS': True, 'server.enableXsrfProtection': True},
    {'server.address': '127.0.0.1', 'server.enableCORS': False, 'server.enableXsrfProtection': True},
    {'server.address': '127.0.0.1', 'server.enableCORS': True, 'server.enableXsrfProtection': False},
])
def test_unprotected_server_cannot_reach_database(settings, monkeypatch):
    import campaigniq.admin_preview as module
    monkeypatch.delenv('RENDER', raising=False)
    monkeypatch.delenv('RENDER_SERVICE_ID', raising=False)
    monkeypatch.setattr(module, 'preview_store', lambda c: pytest.fail('Unsafe preview reached database'))
    errors = []
    class UI:
        def get_option(self, key):
            return settings[key]
        def error(self, value):
            errors.append(value)
    run_app(UI())
    assert errors


def test_private_preview_config_cannot_escape_saved_preview_area(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    root = tmp_path / 'CampaignIQ-Records/Development-Previews/Test'
    root.mkdir(parents=True)
    path = root / 'preview-config.json'
    config = dict(format='campaigniq.admin_preview', version=1, registry_id=str(uuid4()), synthetic_only=True)
    path.write_text(json.dumps(config)); path.chmod(0o600)
    assert load_config(path) == config
    path.chmod(0o644)
    with pytest.raises(ValueError):
        load_config(path)
    other = tmp_path / 'outside.json'; other.write_text(json.dumps(config)); other.chmod(0o600)
    with pytest.raises(ValueError):
        load_config(other)


def test_launcher_creates_private_synthetic_registry_and_resume_does_not_reset_it(tmp_path, monkeypatch):
    import campaigniq.admin_preview as module
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('RENDER', raising=False)
    monkeypatch.delenv('RENDER_SERVICE_ID', raising=False)
    registries = {}
    bootstraps = []
    class Store:
        def __init__(self, dsn, registry_id, *, development_only):
            assert development_only is True
            self.id = registry_id
        def initialize(self):
            pass
        def bootstrap(self, directory):
            bootstraps.append(self.id)
            registries[self.id] = directory
        def _snapshot(self):
            return registries[self.id]
    monkeypatch.setattr(module, 'LocalPostgresAccountDirectory', Store)
    path = module.create_preview()
    config = load_config(path)
    assert len(bootstraps) == 1
    d = registries[config['registry_id']]
    assert all(u.workspace_id.startswith('workspace-preview-') for u in d.users)
    assert all(u.identity.issuer == DEMO_ISSUER for u in d.users)
    updated = d.configure_account(d.users[0].identity, d.accounts[0].id, name='Saved change', expected_revision=0)
    registries[config['registry_id']] = updated
    assert module.preview_store(config)._snapshot() == updated
    assert len(bootstraps) == 1


@pytest.mark.parametrize('field,value', [('synthetic_only', False), ('version', True), ('registry_id', 'not-a-uuid')])
def test_altered_preview_configuration_rejected(tmp_path, monkeypatch, field, value):
    monkeypatch.setenv('HOME', str(tmp_path))
    root = tmp_path / 'CampaignIQ-Records/Development-Previews/Test'; root.mkdir(parents=True)
    config = dict(format='campaigniq.admin_preview', version=1, registry_id=str(uuid4()), synthetic_only=True)
    config[field] = value
    path = root / 'preview-config.json'; path.write_text(json.dumps(config)); path.chmod(0o600)
    with pytest.raises(ValueError):
        load_config(path)
