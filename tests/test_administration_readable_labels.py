from dataclasses import replace
from types import SimpleNamespace
import pytest

from campaigniq.account_administration import Permission
from campaigniq.ui.account_signedin_administration_view import _distinct_labels,directory_labels
from tests.test_account_signedin_admin_view import screen,button,text


def select(at,label):return next(s for s in at.selectbox if s.label==label)


def test_distinct_names_and_statuses_have_no_internal_codes():
    directory=SimpleNamespace(users=(SimpleNamespace(id='private-user-a',name='Dennis',enabled=True),
        SimpleNamespace(id='private-user-b',name='Andrew',enabled=False)),accounts=(
        SimpleNamespace(id='private-account-a',name='Brokerage',owner_id='private-user-a',archived=False),
        SimpleNamespace(id='private-account-b',name='Brokerage',owner_id='private-user-b',archived=True)))
    users,accounts=directory_labels(directory)
    assert users=={'private-user-a':'Dennis','private-user-b':'Andrew [disabled]'}
    assert accounts=={'private-account-a':'Brokerage · Dennis','private-account-b':'Brokerage · Andrew [disabled] [archived]'}


def test_duplicate_user_labels_are_stable_and_avoid_saved_name_collisions():
    names={'private-b':'Owner','private-a':'owner','private-c':'Owner · User 1'}
    labels=_distinct_labels(names,'User')
    assert labels=={'private-a':'owner · User 2','private-b':'Owner · User 3','private-c':'Owner · User 1'}
    assert _distinct_labels(dict(reversed(list(names.items()))),'User')==labels
    assert list(labels)==list(names)
    assert len({n.casefold() for n in labels.values()})==3


def test_same_owner_duplicate_account_labels_remain_distinct():
    directory=SimpleNamespace(users=(SimpleNamespace(id='owner',name='Owner',enabled=True),),accounts=tuple(
        SimpleNamespace(id=i,name='Brokerage',owner_id='owner',archived=False) for i in ('private-b','private-a')))
    _,labels=directory_labels(directory)
    assert labels=={'private-a':'Brokerage · Owner · Account 1','private-b':'Brokerage · Owner · Account 2'}


@pytest.mark.parametrize('deployment',[False,True])
def test_selectors_show_names_and_preserve_id_values_without_render_mutations(deployment):
    at=screen(deployment=deployment);directory=at.session_state['directory']
    assert not at.exception and not at.session_state['calls']
    assert select(at,'User to manage').options==['Owner A','Owner B']
    assert select(at,'New account owner').options==['Owner A','Owner B']
    assert select(at,'Account to manage').options==['Account A','Account B']
    assert select(at,'Account for sharing').options==['Account A','Account B']
    assert select(at,'User receiving access').options==['Owner B']
    assert select(at,'User to manage').value==directory.users[0].id
    assert select(at,'Account to manage').value==directory.accounts[0].id
    assert set(at.dataframe[1].value['Owner'])=={'Owner A','Owner B'}


@pytest.mark.parametrize('deployment',[False,True])
def test_duplicate_user_selection_saves_the_correct_id(deployment):
    at=screen(deployment=deployment);original=at.session_state['directory']
    duplicate=replace(original,users=tuple(replace(u,name='Owner') for u in original.users))
    at.session_state['directory']=duplicate;at.run(timeout=15)
    assert not at.exception
    labels,_=directory_labels(duplicate)
    assert len(set(select(at,'User to manage').options))==2
    target=duplicate.users[1]
    select(at,'User to manage').select(target.id).run(timeout=15)
    assert not at.exception and select(at,'User to manage').options==[labels[u.id] for u in duplicate.users]
    text(at,'Display name').set_value('Renamed second owner');button(at,'Save user').click().run(timeout=15)
    assert not at.exception and not at.error
    command,values=at.session_state['calls'][0]
    assert command=='configure_user' and values['user_id']==target.id
    updated=at.session_state['directory']
    assert updated.users[0]==duplicate.users[0] and updated.users[1].name=='Renamed second owner'
    assert updated.accounts==duplicate.accounts


@pytest.mark.parametrize('deployment',[False,True])
def test_same_owner_duplicate_account_selection_renames_only_selected_record(deployment):
    at=screen(deployment=deployment);original=at.session_state['directory'];owner=original.users[0]
    duplicate=original.add_account(owner.identity,owner.id,name=original.accounts[0].name,
        broker='schwab',expected_revision=original.revision)
    target=duplicate.accounts[-1]
    at.session_state['directory']=duplicate;at.run(timeout=15)
    select(at,'Account to manage').select(target.id).run(timeout=15)
    assert not at.exception
    _,labels=directory_labels(duplicate)
    assert len(set(select(at,'Account to manage').options))==3
    assert select(at,'Account to manage').options==[labels[a.id] for a in duplicate.accounts]
    text(at,'Account name').set_value('Renamed duplicate');button(at,'Save account').click().run(timeout=15)
    assert not at.exception and not at.error
    command,values=at.session_state['calls'][0]
    assert command=='configure_account' and values['account_id']==target.id
    updated=at.session_state['directory']
    assert updated.accounts[:-1]==duplicate.accounts[:-1]
    assert updated.accounts[-1].name=='Renamed duplicate'
    assert updated.accounts[-1].owner_id==target.owner_id
    assert updated.accounts[-1].storage_scope==target.storage_scope


@pytest.mark.parametrize('deployment',[False,True])
def test_sharing_selection_saves_original_account_and_recipient_ids(deployment):
    at=screen(deployment=deployment);directory=at.session_state['directory']
    target=directory.accounts[1];recipient=directory.users[0]
    select(at,'Account for sharing').select(target.id).run(timeout=15)
    assert select(at,'User receiving access').value==recipient.id
    select(at,'Permission').select('View');button(at,'Save access').click().run(timeout=15)
    assert not at.exception and not at.error
    command,values=at.session_state['calls'][0]
    assert command=='set_grant'
    assert values==dict(user_id=recipient.id,account_id=target.id,permission=Permission.VIEW)
    assert at.session_state['directory'].accounts==directory.accounts
