import pytest

from campaigniq.account_administration import Permission
from tests.test_account_signedin_admin_view import screen,button


def select(at,label):return next(s for s in at.selectbox if s.label==label)


def saved_access(at):
    return next(m.value for m in at.markdown if m.value.startswith('Saved access: '))


def seeded(permission, deployment):
    at=screen(deployment=deployment);directory=at.session_state['directory']
    if permission is not None:
        directory=directory.set_grant(directory.users[0].identity,directory.users[1].id,
            directory.accounts[0].id,permission,expected_revision=directory.revision)
        at.session_state['directory']=directory
        button(at,'Reload directory').click().run(timeout=15)
    assert not at.exception and not at.session_state['calls']
    return at,directory


@pytest.mark.parametrize('deployment',[False,True])
@pytest.mark.parametrize('permission,label',[(None,'No shared access'),(Permission.VIEW,'View'),(Permission.EDIT,'Edit')])
def test_saved_access_matches_selected_pair_without_writes(deployment,permission,label):
    at,directory=seeded(permission,deployment)
    assert saved_access(at)=='Saved access: '+label
    assert select(at,'Permission').value==label
    assert select(at,'Account for sharing').value==directory.accounts[0].id
    assert select(at,'User receiving access').value==directory.users[1].id
    assert not at.session_state['calls'] and at.session_state['directory']==directory
    captions='\n'.join(c.value for c in at.caption)
    assert 'Account owner: Owner A. Owners do not need shared access.' in captions
    assert 'Changes take effect only after you select Save access.' in captions
    if deployment:
        assert 'Published months remain read-only.' in captions
        assert 'unpublished reporting months' in captions
        assert 'Existing accounts remain read-only.' not in captions


@pytest.mark.parametrize('deployment',[False,True])
@pytest.mark.parametrize('before,after,label',[(None,Permission.VIEW,'View'),(Permission.VIEW,Permission.EDIT,'Edit'),(Permission.EDIT,None,'No shared access')])
def test_saved_access_changes_only_after_explicit_save(deployment,before,after,label):
    at,directory=seeded(before,deployment)
    old=saved_access(at)
    select(at,'Permission').select(label)
    # An unrelated rerun must not turn the pending form choice into a grant.
    at.run(timeout=15)
    assert not at.exception and not at.session_state['calls']
    assert at.session_state['directory']==directory and saved_access(at)==old
    select(at,'Permission').select(label)
    button(at,'Save access').click().run(timeout=15)
    assert not at.exception and not at.error
    assert saved_access(at)=='Saved access: '+label
    assert at.session_state['calls']==[('set_grant',dict(user_id=directory.users[1].id,
        account_id=directory.accounts[0].id,permission=after))]
    assert at.session_state['directory'].accounts==directory.accounts


@pytest.mark.parametrize('deployment',[False,True])
def test_stale_editor_cannot_overwrite_new_saved_access(deployment):
    at,directory=seeded(Permission.VIEW,deployment)
    changed=directory.set_grant(directory.users[0].identity,directory.users[1].id,
        directory.accounts[0].id,Permission.EDIT,expected_revision=directory.revision)
    at.session_state['directory']=changed
    select(at,'Permission').select('No shared access');button(at,'Save access').click().run(timeout=15)
    assert not at.exception and at.error
    assert not at.session_state['calls'] and at.session_state['directory']==changed
    assert saved_access(at)=='Saved access: Edit'


@pytest.mark.parametrize('deployment',[False,True])
def test_switching_accounts_shows_the_correct_saved_access(deployment):
    at,directory=seeded(Permission.EDIT,deployment)
    assert saved_access(at)=='Saved access: Edit'
    select(at,'Account for sharing').select(directory.accounts[1].id).run(timeout=15)
    assert not at.exception and saved_access(at)=='Saved access: No shared access'
    assert select(at,'User receiving access').value==directory.users[0].id
    select(at,'Account for sharing').select(directory.accounts[0].id).run(timeout=15)
    assert not at.exception and saved_access(at)=='Saved access: Edit'
    assert select(at,'User receiving access').value==directory.users[1].id
    assert not at.session_state['calls'] and at.session_state['directory']==directory


@pytest.mark.parametrize('deployment',[False,True])
@pytest.mark.parametrize('limitation',['disabled','archived'])
def test_saved_grant_does_not_imply_disabled_or_archived_write_access(deployment,limitation):
    at,directory=seeded(Permission.EDIT,deployment);actor=directory.users[0].identity
    if limitation=='disabled':
        changed=directory.configure_user(actor,directory.users[1].id,enabled=False,expected_revision=directory.revision)
        expected='This user is disabled and cannot access the account while disabled.'
    else:
        changed=directory.configure_account(actor,directory.accounts[0].id,archived=True,expected_revision=directory.revision)
        expected='Archived accounts are read-only.'
    at.session_state['directory']=changed;button(at,'Reload directory').click().run(timeout=15)
    assert not at.exception and not at.session_state['calls']
    assert saved_access(at)=='Saved access: Edit'
    assert expected in [c.value for c in at.caption]
    assert at.session_state['directory']==changed
