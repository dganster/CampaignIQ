import pytest
from streamlit.testing.v1 import AppTest


def screen(*, member=False, deployment=False):
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
render(st, Service(), RehearsalSession(''' + ('B' if member else 'A') + '''), deployment=''' + repr(deployment) + ''')
''').run(timeout=15)


def button(at, label):
    return next(b for b in at.button if b.label == label)


def text(at, label):
    return next(t for t in at.text_input if t.label == label)


@pytest.mark.parametrize('deployment',[False,True])
def test_admin_panel_uses_signin_without_actor_selector(deployment):
    at = screen(deployment=deployment)
    assert not at.exception
    if deployment:
        assert [h.value for h in at.header]==['Administration']
        assert 'Account administration' not in [e.label for e in at.expander]
        details=next(e for e in at.expander if e.label=='Directory details')
        assert any(b.label=='Reload directory' for b in details.button)
        assert any(c.value.startswith('Directory revision ') for c in details.caption)
    assert [tab.label for tab in at.tabs] == ['Users', 'Brokerage accounts', 'Account access', 'Activity']
    assert 'Demo user' not in [s.label for s in at.selectbox]
    assert not any('publish' in b.label.lower() for b in at.button)


@pytest.mark.parametrize('deployment',[False,True])
def test_member_has_no_admin_directory_widgets(deployment):
    at = screen(member=True,deployment=deployment)
    assert not at.exception and not at.tabs and not at.button and not at.dataframe


@pytest.mark.parametrize('deployment',[False,True])
def test_rename_user_saves_and_refreshes_once(deployment):
    at = screen(deployment=deployment)
    text(at, 'Display name').set_value('Dennis local test')
    button(at, 'Save user').click().run(timeout=15)
    assert not at.exception
    assert at.session_state['directory'].users[0].name == 'Dennis local test'
    assert len(at.session_state['calls']) == 1
    assert at.success


@pytest.mark.parametrize('deployment',[False,True])
def test_rename_account_preserves_legacy_namespace(deployment):
    at = screen(deployment=deployment)
    text(at, 'Account name').set_value('Taxable account')
    button(at, 'Save account').click().run(timeout=15)
    assert not at.exception
    account = at.session_state['directory'].accounts[0]
    assert account.name == 'Taxable account' and account.storage_scope == 'workspace-a'


@pytest.mark.parametrize('deployment',[False,True])
def test_new_account_is_metadata_only_and_does_not_replace_existing(deployment):
    at = screen(deployment=deployment)
    original = at.session_state['directory'].accounts
    text(at, 'New account name').set_value('Second brokerage')
    button(at, 'Add account').click().run(timeout=15)
    assert not at.exception
    directory = at.session_state['directory']
    assert directory.accounts[:2] == original and len(directory.accounts) == 3
    assert not directory.accounts[-1].legacy


@pytest.mark.parametrize('deployment',[False,True])
def test_registration_uses_gmail_invitation_without_subject_fields(deployment):
    at = screen(deployment=deployment)
    assert 'Verified Google subject' not in [t.label for t in at.text_input]
    text(at, 'New user display name').set_value('Matthew')
    text(at, 'Gmail address').set_value('matthew@gmail.com')
    button(at, 'Save invitation').click().run(timeout=15)
    assert not at.exception and not at.error
    assert len(at.session_state['directory'].users) == 2
    assert at.session_state['calls'] == [('invite_google_user',dict(name='Matthew',email='matthew@gmail.com'))]


@pytest.mark.parametrize('deployment',[False,True])
def test_last_admin_guard_shown_without_mutation(deployment):
    at = screen(deployment=deployment)
    next(c for c in at.checkbox if c.label == 'Administrator').uncheck()
    button(at, 'Save user').click().run(timeout=15)
    assert not at.exception and at.error
    assert at.session_state['directory'].users[0].admin
    assert not at.session_state['calls']


@pytest.mark.parametrize('deployment',[False,True])
def test_stale_form_does_not_overwrite_concurrent_change(deployment):
    at = screen(deployment=deployment)
    directory = at.session_state['directory']
    actor = directory.users[0]
    changed = directory.configure_user(actor.identity, actor.id, name='Concurrent change', expected_revision=0)
    at.session_state['directory'] = changed
    text(at, 'Display name').set_value('Stale form')
    button(at, 'Save user').click().run(timeout=15)
    assert not at.exception and at.error
    assert at.session_state['directory'] == changed
    assert not at.session_state['calls']


@pytest.mark.parametrize('deployment',[False,True])
def test_reload_refreshes_stale_forms_without_saving(deployment):
    at=screen(deployment=deployment)
    directory=at.session_state['directory'];actor=directory.users[0]
    changed=directory.configure_user(actor.identity,actor.id,name='Concurrent change',expected_revision=0)
    at.session_state['directory']=changed
    text(at,'Display name').set_value('Unsaved edit')
    button(at,'Reload directory').click().run(timeout=15)
    assert not at.exception and not at.session_state['calls']
    assert text(at,'Display name').value=='Concurrent change'
    text(at,'Display name').set_value('Fresh edit')
    button(at,'Save user').click().run(timeout=15)
    assert not at.exception and not at.error
    assert len(at.session_state['calls'])==1
    assert at.session_state['directory'].users[0].name=='Fresh edit'
