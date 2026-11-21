from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import pytest

from campaigniq.account_administration import Identity,LegacyOwner,Permission,propose_legacy_directory
from campaigniq.broker_account_binding import bind,bindings
from campaigniq.ui.account_signedin_administration_view import activity_rows,directory_labels
from tests.test_account_signedin_admin_view import screen


def recorded_directory():
    issuer='https://accounts.example.test';a=Identity(issuer,'a');b=Identity(issuer,'b')
    directory=propose_legacy_directory((LegacyOwner(a,'workspace-a','Owner A','Account A','schwab'),
        LegacyOwner(b,'workspace-b','Owner B','Account B','schwab')),a)
    account=directory.accounts[0].id;member=directory.users[1].id
    directory=bind(directory,a,account,'securities','12345678',expected_revision=directory.revision)
    directory=bind(directory,a,account,'forex','9876543210',expected_revision=directory.revision)
    directory=directory.set_grant(a,member,account,Permission.EDIT,expected_revision=directory.revision)
    directory=directory.configure_account(a,account,name='Earlier account name',archived=False,expected_revision=directory.revision)
    directory=directory.configure_user(a,member,name='Earlier user name',enabled=True,admin=False,expected_revision=directory.revision)
    directory=directory.add_user(a,Identity(issuer,'third'),'Third user',expected_revision=directory.revision)
    directory=directory.add_account(a,directory.users[-1].id,'Third account','schwab',expected_revision=directory.revision)
    directory=directory.set_grant(a,member,account,None,expected_revision=directory.revision)
    directory=directory.configure_user(a,member,name='Current user name',expected_revision=directory.revision)
    return directory


def test_all_known_actions_have_readable_names_and_summaries_without_mutation():
    directory=recorded_directory();before=deepcopy(directory);original_bindings=bindings(directory,directory.accounts[0].id)
    rows=activity_rows(directory)
    assert [r['Event'] for r in rows]==[e.sequence for e in directory.events]
    assert {r['Action'] for r in rows}=={'Broker account linked','Access granted','Access removed',
        'Account settings saved','User settings saved','User registered','Account added'}
    assert rows[0]['Summary']=='Schwab; Equities and options; ending 5678'
    assert rows[1]['Summary']=='Schwab; Forex; ending 3210'
    assert rows[2]['Summary']=='Edit access for Current user name'
    assert rows[3]['Summary']=='Name: Earlier account name; Active'
    assert rows[4]['Summary']=='Display name: Earlier user name; Enabled; Member'
    assert rows[5]['Summary']=='Display name: Third user'
    assert rows[6]['Summary']=='Name: Third account; Owner: Third user; Broker: Schwab'
    assert rows[7]['Summary']=='Shared access removed for Current user name'
    assert rows[4]['User or account']=='Current user name'
    assert directory==before and bindings(directory,directory.accounts[0].id)==original_bindings
    display=json.dumps(rows)
    for item in (*directory.users,*directory.accounts):
        assert item.id not in display and item.id[:8] not in display
    for binding in original_bindings.values():assert binding.digest not in display


def test_unknown_actions_or_missing_records_never_guess_a_name_or_expose_codes():
    directory=recorded_directory();event=SimpleNamespace(sequence=100,actor_id='private-actor',
        target_id='private-target',action='future_internal_action_v2',details=(('digest','private-digest'),))
    rows=activity_rows(SimpleNamespace(users=directory.users,accounts=directory.accounts,events=(event,)))
    assert rows[0]['Changed by']=='Unavailable user'
    assert rows[0]['User or account']=='Unavailable user or account'
    assert rows[0]['Action']=='Other administration activity'
    assert 'private' not in json.dumps(rows) and 'future_internal_action' not in json.dumps(rows)


def test_duplicate_and_disabled_actor_labels_are_consistent_with_selectors():
    directory=recorded_directory()
    directory=replace(directory,users=tuple(replace(u,name='Same name') for u in directory.users))
    users,accounts=directory_labels(directory);rows=activity_rows(directory)
    assert rows[0]['Changed by']==users[directory.events[0].actor_id]
    assert rows[0]['User or account']==accounts[directory.events[0].target_id]
    directory=replace(directory,users=(replace(directory.users[0],enabled=False,admin=False),
        replace(directory.users[1],admin=True),directory.users[2]))
    assert '[disabled]' in activity_rows(directory)[0]['Changed by']


@pytest.mark.parametrize('deployment',[False,True])
def test_activity_view_preserves_events_and_exposes_exact_raw_details_separately(deployment):
    at=screen(deployment=deployment);directory=recorded_directory();before=deepcopy(directory)
    at.session_state['directory']=directory;at.run(timeout=15)
    assert not at.exception and not at.session_state['calls']
    assert at.session_state['directory']==before
    table=next(t for t in at.dataframe if 'Changed by' in t.value.columns)
    assert list(table.value.columns)==['Event','Changed by','Action','User or account','Summary']
    assert table.value.to_dict('records')==activity_rows(directory)
    technical=next(e for e in at.expander if e.label=='Technical audit details')
    assert not technical.proto.expanded
    selected=json.loads(technical.json[0].value);event=directory.events[0]
    assert selected==dict(sequence=event.sequence,actor_id=event.actor_id,action=event.action,
        target_id=event.target_id,details=dict(event.details))
    selector=next(s for s in at.selectbox if s.label=='Activity event')
    selector.select(8).run(timeout=15)
    assert not at.exception and not at.session_state['calls']
    technical=next(e for e in at.expander if e.label=='Technical audit details')
    assert json.loads(technical.json[0].value)['action']=='revoke_access'
    assert any(m.value=='Summary: Shared access removed for Current user name' for m in at.markdown)
    assert at.session_state['directory']==before


@pytest.mark.parametrize('deployment',[False,True])
def test_empty_activity_has_a_readable_empty_state_without_new_events(deployment):
    at=screen(deployment=deployment)
    assert not at.exception and not at.session_state['calls']
    assert 'No administration activity has been recorded.' in [i.value for i in at.info]
    assert not any(s.label=='Activity event' for s in at.selectbox)
    assert not at.session_state['directory'].events
