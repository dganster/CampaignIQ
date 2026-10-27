from streamlit.testing.v1 import AppTest


def screen(*, member=False):
    return AppTest.from_string('''
import streamlit as st
from campaigniq.account_administration import Identity, LegacyOwner, propose_legacy_directory
from campaigniq.account_legacy_access_rehearsal import RehearsalSession
from campaigniq.ui.account_signedin_administration_view import render
ISSUER = 'https://accounts.example.test'
A, B = Identity(ISSUER, 'a'), Identity(ISSUER, 'b')
if 'directory' not in st.session_state:
    st.session_state.directory = propose_legacy_directory((
        LegacyOwner(A, 'workspace-a', 'Owner A', 'Account A', 'schwab'),
        LegacyOwner(B, 'workspace-b', 'Owner B', 'Account B', 'schwab')), A)
    st.session_state.calls = []
class Service:
    _issuer = ISSUER
    registry_id = '00000000-0000-0000-0000-000000000001'
    def snapshot(self, user):
        actor = st.session_state.directory.user(Identity(user.to_dict()['iss'], user.to_dict()['sub']))
        if not actor.admin: raise PermissionError('admin required')
        return st.session_state.directory
    def execute(self, user, revision, command, **values):
        identity = Identity(user.to_dict()['iss'], user.to_dict()['sub'])
        updated = getattr(st.session_state.directory, command)(identity, expected_revision=revision, **values)
        st.session_state.directory = updated
        st.session_state.calls.append((command, values))
    def add_user(self, user, revision, *, name, subject):
        self.execute(user, revision, 'add_user', name=name, identity=Identity(ISSUER, subject))
render(st, Service(), RehearsalSession(''' + ('B' if member else 'A') + '''))
''').run(timeout=15)


def button(at, label):
    return next(b for b in at.button if b.label == label)


def text(at, label):
    return next(t for t in at.text_input if t.label == label)


def test_admin_panel_uses_signin_without_actor_selector():
    at = screen()
    assert not at.exception
    assert [tab.label for tab in at.tabs] == ['Users', 'Brokerage accounts', 'Account access', 'Activity']
    assert 'Demo user' not in [s.label for s in at.selectbox]
    assert not any('publish' in b.label.lower() for b in at.button)


def test_member_has_no_admin_directory_widgets():
    at = screen(member=True)
    assert not at.exception and not at.tabs and not at.button and not at.dataframe


def test_rename_user_saves_and_refreshes_once():
    at = screen()
    text(at, 'Display name').set_value('Dennis local test')
    button(at, 'Save user').click().run(timeout=15)
    assert not at.exception
    assert at.session_state['directory'].users[0].name == 'Dennis local test'
    assert len(at.session_state['calls']) == 1
    assert at.success


def test_rename_account_preserves_legacy_namespace():
    at = screen()
    text(at, 'Account name').set_value('Taxable account')
    button(at, 'Save account').click().run(timeout=15)
    assert not at.exception
    account = at.session_state['directory'].accounts[0]
    assert account.name == 'Taxable account' and account.storage_scope == 'workspace-a'


def test_new_account_is_metadata_only_and_does_not_replace_existing():
    at = screen()
    original = at.session_state['directory'].accounts
    text(at, 'New account name').set_value('Second brokerage')
    button(at, 'Add account').click().run(timeout=15)
    assert not at.exception
    directory = at.session_state['directory']
    assert directory.accounts[:2] == original and len(directory.accounts) == 3
    assert not directory.accounts[-1].legacy


def test_registration_requires_explicit_verified_subject_confirmation():
    at = screen()
    text(at, 'New user display name').set_value('New verified person')
    text(at, 'Verified Google subject').set_value('new-subject')
    button(at, 'Register user').click().run(timeout=15)
    assert not at.exception and at.error
    assert len(at.session_state['directory'].users) == 2
    assert not at.session_state['calls']


def test_last_admin_guard_shown_without_mutation():
    at = screen()
    next(c for c in at.checkbox if c.label == 'Administrator').uncheck()
    button(at, 'Save user').click().run(timeout=15)
    assert not at.exception and at.error
    assert at.session_state['directory'].users[0].admin
    assert not at.session_state['calls']


def test_stale_form_does_not_overwrite_concurrent_change():
    at = screen()
    directory = at.session_state['directory']
    actor = directory.users[0]
    changed = directory.configure_user(actor.identity, actor.id, name='Concurrent change', expected_revision=0)
    at.session_state['directory'] = changed
    text(at, 'Display name').set_value('Stale form')
    button(at, 'Save user').click().run(timeout=15)
    assert not at.exception and at.error
    assert at.session_state['directory'] == changed
    assert not at.session_state['calls']
