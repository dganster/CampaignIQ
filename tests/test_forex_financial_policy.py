from copy import deepcopy
import json
import pytest

from campaigniq import account_forex_financial_policy as policy
from campaigniq import account_forex_daily_sources as daily
from campaigniq.account_administration import Permission
from tests.test_forex_daily_source_receipts import confirmed, repo, revision, save as save_daily
from tests.test_account_forex_cash_receipts import sources, saving, bound_context, context
from tests.test_account_forex_inventory_preview import Storage


class FinancialStorage(Storage):
    def list_keys(self,*,prefix='',suffix=''):
        return tuple(sorted(k for k in self.rows if k.startswith(prefix) and k.endswith(suffix)))


@pytest.fixture
def ready(confirmed):
    c=confirmed;c.artifacts[c.account]=FinancialStorage();save_daily(c)
    return c


def review(c,user=None):
    return c.associated.forex_financial_candidate_preview(user or c.admin,c.account,c.selected.id,revision(c))


def approve(c,**kwargs):
    args=dict(reviewed_candidate_sha256=review(c)['artifact_sha256'],confirm_daily_basis=True,
        confirm_retained_differences=True,confirm_coverage_limits=True)
    args.update(kwargs)
    return c.associated.save_forex_financial_policy(c.admin,c.account,c.selected.id,revision(c),**args)


def saved(c,user=None):
    return c.associated.forex_financial_policy_preview(user or c.admin,c.account,c.selected.id,revision(c))


def test_owner_approval_binds_displayed_hash_and_survives_its_own_revision_increment(ready):
    c=ready;before=deepcopy(repo(c).__dict__);financial=deepcopy(c.artifacts[c.account].rows)
    original=review(c);old=revision(c)
    assert approve(c)==old+1
    result=saved(c);receipt=result['receipt']
    assert receipt['confirmed_by']==c.directory.user(c.admin).id
    assert receipt['confirmed_source_revision']==old
    assert receipt['reviewed_candidate_sha256']==original['artifact_sha256']
    assert result['candidate']['artifact_sha256']!=original['artifact_sha256']
    assert receipt['stable_candidate_sha256']==policy.stable_candidate(result['candidate']['artifact'])
    assert result['financial_policy_approved'] and not result['original_discrepancies_resolved']
    assert result['candidate']['artifact']['financial_policy_approved'] is False
    assert result['publication_authorized'] is result['financial_state_written'] is False
    assert all(repo(c).docs[k]==v for k,v in before['docs'].items())
    after=deepcopy(repo(c).__dict__)
    assert approve(c)==old+1 and repo(c).__dict__==after
    assert c.artifacts[c.account].rows==financial
    assert all(len(value)==64 for value in receipt['binding_sha256'].values())


@pytest.mark.parametrize('choice',[{'confirm_daily_basis':False},{'confirm_daily_basis':1},
    {'confirm_retained_differences':False},{'confirm_coverage_limits':False},{'reviewed_candidate_sha256':'bad'}])
def test_explicit_choices_rejected_before_connection(choice):
    from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
    obj=object.__new__(DeploymentMonthlyInputs)
    args=dict(reviewed_candidate_sha256='a'*64,confirm_daily_basis=True,confirm_retained_differences=True,confirm_coverage_limits=True)
    args.update(choice)
    with pytest.raises(ValueError):obj.save_forex_financial_policy(None,'a','d',0,**args)


@pytest.mark.parametrize('case',['stale','review','foreign','published','upload','postwrite'])
def test_rejected_or_failed_approval_writes_no_receipt_or_revision(ready,monkeypatch,case):
    c=ready;hash=review(c)['artifact_sha256'];rev=revision(c);user=c.admin
    if case=='stale':rev-=1
    elif case=='review':hash='0'*64
    elif case=='foreign':user=c.member
    elif case=='published':c.artifacts[c.account].rows['2026-10-sentinel.json']='{}'
    else:
        real=repo(c).upload
        def fail(did,role,*args,**kwargs):
            result=real(did,role,*args,**kwargs)
            if role==policy.ROLE:
                if case=='upload':raise RuntimeError('Injected approval failure')
                name,media,raw=repo(c).docs[did,role]
                body=json.loads(raw);body['stable_candidate_sha256']='0'*64
                repo(c).docs[did,role]=(name,media,json.dumps(body).encode())
            return result
        monkeypatch.setattr(repo(c),'upload',fail)
    before=deepcopy((repo(c).rows,repo(c).docs,repo(c).originals,c.artifacts[c.account].rows))
    with pytest.raises((ValueError,PermissionError,RuntimeError)):
        c.associated.save_forex_financial_policy(user,c.account,c.selected.id,rev,
            reviewed_candidate_sha256=hash,confirm_daily_basis=True,confirm_retained_differences=True,confirm_coverage_limits=True)
    assert (repo(c).rows,repo(c).docs,repo(c).originals,c.artifacts[c.account].rows)==before


@pytest.mark.parametrize('field,value',[('account_id','foreign'),('draft_id','foreign'),('version',True),
    ('confirmed_source_revision',True),('confirmed_by','foreign-owner'),('confirmed_at','2026-10-01'),
    ('policy_id','other-policy'),('reviewed_candidate_sha256','0'*64),('stable_candidate_sha256','0'*64),
    ('daily_source_receipt_sha256','0'*64),('daily_reporting_session_basis_accepted',False),
    ('original_differences_retained',False),('coverage_limits_accepted',False),('financial_policy_approved',1),
    ('publication_authorized',True),('original_discrepancies_resolved',True),('predecessor_artifact_sha256',{}),
    ('binding_sha256',{}),('original_source_sha256',{})])
def test_corrupt_approval_withholds_saved_review_and_cannot_be_overwritten_as_repair(ready,field,value):
    c=ready;approve(c);name,media,raw=repo(c).document(c.selected.id,policy.ROLE)
    body=json.loads(raw);body[field]=value
    repo(c).docs[c.selected.id,policy.ROLE]=(name,media,json.dumps(body).encode())
    before=deepcopy(repo(c).__dict__)
    with pytest.raises(ValueError):saved(c)
    with pytest.raises(ValueError):approve(c)
    assert repo(c).__dict__==before


def test_changed_daily_source_and_revoked_access_do_not_repair_an_approval(ready):
    c=ready;approve(c)
    member=c.directory.user(c.member).id
    c.directory=c.directory.set_grant(c.admin,member,c.account,Permission.VIEW,expected_revision=c.directory.revision)
    assert saved(c,c.member)['financial_policy_approved']
    c.directory=c.directory.set_grant(c.admin,member,c.account,Permission.EDIT,expected_revision=c.directory.revision)
    with pytest.raises(PermissionError,match='owner'):
        c.associated.save_forex_financial_policy(c.member,c.account,c.selected.id,revision(c),
            reviewed_candidate_sha256=review(c)['artifact_sha256'],confirm_daily_basis=True,
            confirm_retained_differences=True,confirm_coverage_limits=True)
    c.directory=c.directory.set_grant(c.admin,member,c.account,None,expected_revision=c.directory.revision)
    with pytest.raises(PermissionError):saved(c,c.member)
    role=daily.PREFIX+'2026-10-01';name,media,raw=repo(c).document(c.selected.id,role)
    repo(c).docs[c.selected.id,role]=(name,media,raw+b'\nchanged')
    before=deepcopy(repo(c).__dict__)
    with pytest.raises(ValueError):saved(c)
    with pytest.raises(ValueError):approve(c)
    assert repo(c).__dict__==before


def test_unclassified_financing_cannot_receive_policy_approval(ready,monkeypatch):
    c=ready
    from campaigniq import account_forex_financial_candidate as financial
    real=financial.preview
    def unknown(*args):
        result=real(*args);result['artifact']['calculation']['all_original_financing_rows_classified']=False
        return result
    monkeypatch.setattr(financial,'preview',unknown)
    before=deepcopy(repo(c).__dict__)
    with pytest.raises(ValueError,match='classified'):approve(c)
    assert repo(c).__dict__==before


def test_unrelated_source_revision_does_not_rewrite_approval(ready):
    c=ready;approve(c);raw=repo(c).document(c.selected.id,policy.ROLE)
    repo(c).upload(c.selected.id,'synthetic-unrelated-note','note.txt','text/plain',b'note')
    assert saved(c)['financial_policy_approved']
    assert repo(c).document(c.selected.id,policy.ROLE)==raw


def test_previous_displayed_hash_cannot_be_reused_at_new_revision(ready):
    c=ready;old=review(c)['artifact_sha256'];approve(c);before=deepcopy(repo(c).__dict__)
    with pytest.raises(ValueError,match='review'):approve(c,reviewed_candidate_sha256=old)
    assert repo(c).__dict__==before


def test_duplicate_approval_fields_are_rejected_without_repair(ready):
    c=ready;approve(c);name,media,raw=repo(c).document(c.selected.id,policy.ROLE)
    body=raw.decode().rstrip();body=body[:-1]+',"publication_authorized":false}'
    repo(c).docs[c.selected.id,policy.ROLE]=(name,media,body.encode())
    before=deepcopy(repo(c).__dict__)
    with pytest.raises(ValueError,match='Duplicate'):saved(c)
    with pytest.raises(ValueError):approve(c)
    assert repo(c).__dict__==before


def test_application_delegates_saved_approval_and_save_with_explicit_choices():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from campaigniq.account_deployment_application import DeploymentMonthlyApplication
    app=object.__new__(DeploymentMonthlyApplication)
    app._inputs=SimpleNamespace(save_forex_financial_policy=Mock(return_value=6),forex_financial_policy_preview=Mock(return_value='saved'))
    choices=dict(reviewed_candidate_sha256='a'*64,confirm_daily_basis=True,confirm_retained_differences=True,confirm_coverage_limits=True)
    assert app.save_forex_financial_policy('u','a','d',5,**choices)==6
    app._inputs.save_forex_financial_policy.assert_called_once_with('u','a','d',5,**choices)
    assert app.forex_financial_policy_preview('u','a','d',6)=='saved'
