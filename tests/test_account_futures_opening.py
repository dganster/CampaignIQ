"""Account-scoped user assertions, never broker evidence or publication authority."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from campaigniq.account_futures_opening import ROLE, load_receipt
from campaigniq.account_administration import Permission
from campaigniq.import_draft_bridge import RestoredUpload
from tests.test_gain_loss_association import saving, context, bound_context
from tests.test_futures_accounting import write_source


@pytest.fixture
def opening(saving,tmp_path):
    c=saving
    text=write_source(tmp_path).read_text().replace('Account Statement for synthetic',
        'Account Statement for 12345678 since 9/30/26 through 10/31/26')
    c.history=text.encode()
    c.associated.save(c.admin,c.account,c.selected.id,1,'thinkorswim_trade_history',
        RestoredUpload('history.csv','text/csv',c.history))
    return c


def confirm(c,**kwargs):
    revision=c.repositories[c.account].get(c.selected.id).revision
    return c.associated.confirm_empty_futures_opening(c.admin,c.account,c.selected.id,revision,
        confirm_empty_opening=True,**kwargs)


def status(c,user=None):
    repo=c.repositories[c.account]
    return c.associated.futures_opening_status(user or c.admin,c.account,c.selected.id,repo.get(c.selected.id).revision)


def test_confirmation_scopes_assertion_and_preserves_originals(opening):
    c=opening;repo=c.repositories[c.account];before=dict(repo.docs)
    assert not status(c)['confirmed']
    assert confirm(c)==3
    result=status(c)
    assert result==dict(confirmed=True,evidence_type='user_confirmation',broker_verified=False,
        publication_authorized=False,opening_as_of='2026-09-30')
    for key,value in before.items():assert repo.docs[key]==value
    assert set(repo.docs)-set(before)=={(c.selected.id,ROLE)}
    receipt=json.loads(repo.document(c.selected.id,ROLE)[2])
    assert receipt['confirmed_by']==c.directory.user(c.admin).id
    assert receipt['account_id']==c.account and receipt['draft_id']==str(c.selected.id)
    assert '12345678' not in json.dumps(receipt)
    assert not repo.originals


def test_exact_repeat_is_idempotent(opening):
    c=opening;confirm(c);repo=c.repositories[c.account];before=deepcopy(repo.__dict__)
    assert confirm(c)==3 and repo.__dict__==before


@pytest.mark.parametrize('value',[False,None,1,'yes'])
def test_explicit_literal_confirmation_required_before_connection(value):
    from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
    service=object.__new__(DeploymentMonthlyInputs)
    with pytest.raises(ValueError,match='Explicit'):
        service.confirm_empty_futures_opening(None,'a','d',0,confirm_empty_opening=value)


@pytest.mark.parametrize('revision',[True,-1,None,'2'])
def test_invalid_revisions_precede_connections(revision):
    from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
    service=object.__new__(DeploymentMonthlyInputs)
    with pytest.raises(ValueError):service.confirm_empty_futures_opening(None,'a','d',revision,confirm_empty_opening=True)
    with pytest.raises(ValueError):service.futures_opening_status(None,'a','d',revision)


@pytest.mark.parametrize('case',['stale','foreign','published','write_failure','post_write_failure'])
def test_failures_leave_all_rows_unchanged(opening,monkeypatch,case):
    c=opening;repo=c.repositories[c.account];before=deepcopy(repo.__dict__)
    user=c.admin;revision=2
    if case=='stale':revision=1
    elif case=='foreign':user=c.member
    elif case=='published':monkeypatch.setattr(c.artifacts[c.account],'list_keys',lambda **kw:('published',))
    else:
        actual=repo.upload
        def upload(*args,**kw):
            if case=='write_failure':raise ValueError('Injected write failure')
            result=actual(*args,**kw)
            repo.docs[c.selected.id,ROLE]=('bad.json','text/plain',b'{}')
            return result
        monkeypatch.setattr(repo,'upload',upload)
        before=deepcopy(repo.__dict__)
    with pytest.raises((ValueError,PermissionError)):
        c.associated.confirm_empty_futures_opening(user,c.account,c.selected.id,revision,confirm_empty_opening=True)
    current=c.repositories[c.account]
    assert current.rows==before['rows'] and current.docs==before['docs'] and current.originals==before['originals']


@pytest.mark.parametrize('case',['history','binding'])
def test_changed_evidence_invalidates_without_repair(opening,case):
    c=opening;confirm(c);repo=c.repositories[c.account]
    if case=='history':
        repo.upload(c.selected.id,'thinkorswim_trade_history','changed.csv','text/csv',c.history+b'\n',replace=True)
    else:
        from campaigniq.broker_account_binding import bind
        c.directory=bind(c.directory,c.admin,c.account,'securities','87654321',expected_revision=1,confirm_replace=True)
    before=dict(repo.docs)
    with pytest.raises(ValueError):status(c)
    assert repo.docs==before


def test_explicit_reconfirmation_after_history_change_preserves_prior_receipt(opening):
    c=opening;confirm(c);repo=c.repositories[c.account]
    previous=repo.document(c.selected.id,ROLE)
    repo.upload(c.selected.id,'thinkorswim_trade_history','changed.csv','text/csv',c.history+b'\n',replace=True)
    assert confirm(c)==5 and status(c)['confirmed']
    assert previous in repo.originals


@pytest.mark.parametrize('field,value',[
    ('account_id','foreign'),('draft_id','foreign'),('year',2025),('month',9),
    ('opening_as_of','2026-10-01'),('version',True),('empty_futures',1),
    ('empty_futures_options',False),('evidence_type','broker_verified'),('broker_verified',True),
    ('history_sha256','bad'),('binding_digest','bad'),('confirmed_at','2026-10-01'),('confirmed_by',''),
    ('confirmed_source_revision',True),('confirmed_source_revision',3),
])
def test_corrupt_scope_or_evidence_cannot_be_read_or_overwritten(opening,field,value):
    c=opening;confirm(c);repo=c.repositories[c.account]
    receipt=json.loads(repo.document(c.selected.id,ROLE)[2]);receipt[field]=value
    repo.docs[c.selected.id,ROLE]=('bad.json','text/plain',json.dumps(receipt).encode())
    before=deepcopy(repo.docs)
    with pytest.raises(ValueError):status(c)
    with pytest.raises(ValueError):confirm(c)
    assert repo.docs==before


def test_duplicate_json_fields_cannot_hide_corruption(opening):
    c=opening;confirm(c);repo=c.repositories[c.account]
    data=repo.document(c.selected.id,ROLE)[2].decode()
    repo.docs[c.selected.id,ROLE]=('bad.json','text/plain',data.rstrip()[:-1]+',"broker_verified": false}')
    with pytest.raises(ValueError,match='Duplicate'):status(c)


def test_view_grant_cannot_confirm_and_revocation_blocks_read(opening):
    c=opening;confirm(c)
    user_id=c.directory.user(c.member).id
    c.directory=c.directory.set_grant(c.admin,user_id,c.account,Permission.VIEW,expected_revision=c.directory.revision)
    assert status(c,c.member)['confirmed']
    with pytest.raises(PermissionError):
        c.associated.confirm_empty_futures_opening(c.member,c.account,c.selected.id,3,confirm_empty_opening=True)
    c.directory=c.directory.set_grant(c.admin,user_id,c.account,None,expected_revision=c.directory.revision)
    with pytest.raises(PermissionError):status(c,c.member)


@pytest.mark.parametrize('case',['missing','wrong_account','coverage','no_futures'])
def test_missing_or_wrong_history_cannot_be_confirmed(opening,case):
    c=opening;repo=c.repositories[c.account]
    if case=='missing':del repo.docs[c.selected.id,'thinkorswim_trade_history']
    else:
        text=c.history.decode()
        if case=='wrong_account':text=text.replace('12345678','87654321')
        elif case=='coverage':text=text.replace('through 10/31/26','through 10/30/26')
        else:text=text.replace('10/5/26 10:00:00','9/5/26 10:00:00')
        repo.docs[c.selected.id,'thinkorswim_trade_history']=('bad.csv','text/csv',text.encode())
    before=dict(repo.docs)
    with pytest.raises(ValueError):confirm(c)
    assert repo.docs==before


def test_application_delegates_without_publisher():
    from campaigniq.account_deployment_application import DeploymentMonthlyApplication
    calls=[]
    backend=SimpleNamespace(gateway=SimpleNamespace(_directory=None),
        confirm_empty_futures_opening=lambda *a,**k:calls.append(('confirm',a,k)),
        futures_opening_status=lambda *a,**k:calls.append(('status',a,k)))
    application=DeploymentMonthlyApplication(backend,None,publication_enabled=False)
    application.confirm_empty_futures_opening('u','a','d',2,confirm_empty_opening=True)
    application.futures_opening_status('u','a','d',3)
    assert [c[0] for c in calls]==['confirm','status'] and application.publication_enabled is False


def test_editor_cannot_supply_owner_assertion(opening):
    c=opening
    c.directory=c.directory.set_grant(c.admin,c.directory.user(c.member).id,c.account,Permission.EDIT,expected_revision=c.directory.revision)
    before=deepcopy(c.repositories[c.account].docs)
    with pytest.raises(PermissionError,match='owner'):
        c.associated.confirm_empty_futures_opening(c.member,c.account,c.selected.id,2,confirm_empty_opening=True)
    assert c.repositories[c.account].docs==before


def test_foreign_draft_cannot_be_confirmed_under_selected_account(opening):
    c=opening
    foreign=c.bound.create(c.member,c.other,2026,10)
    before=deepcopy(c.repositories[c.account].docs)
    with pytest.raises(FileNotFoundError):
        c.associated.confirm_empty_futures_opening(c.admin,c.account,foreign.id,0,confirm_empty_opening=True)
    assert c.repositories[c.account].docs==before


def test_archived_account_keeps_assertion_readable_but_cannot_reconfirm(opening):
    c=opening;confirm(c)
    c.directory=c.directory.configure_account(c.admin,c.account,archived=True,expected_revision=c.directory.revision)
    assert status(c)['confirmed']
    with pytest.raises(PermissionError):confirm(c)


def test_disabled_viewer_cannot_read_confirmation(opening):
    c=opening;confirm(c)
    user_id=c.directory.user(c.member).id
    c.directory=c.directory.set_grant(c.admin,user_id,c.account,Permission.VIEW,expected_revision=c.directory.revision)
    c.directory=c.directory.configure_user(c.admin,user_id,enabled=False,expected_revision=c.directory.revision)
    with pytest.raises(PermissionError):status(c,c.member)
