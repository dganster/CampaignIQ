from copy import deepcopy
import json
import pytest
from campaigniq import account_forex_cash_review as m
from campaigniq.broker_account_binding import bind
from campaigniq.import_draft_bridge import RestoredUpload
from tests.test_gain_loss_association import saving,bound_context,context
from tests.test_forex_cash_ledger_preview import export,EXEC

@pytest.fixture
def sources(saving):
    c=saving
    c.directory=bind(c.directory,c.admin,c.account,'forex','3607739',expected_revision=c.directory.revision)
    repo=c.repositories[c.account]
    history=export().replace('Account Statement for synthetic','Account Statement for 12345678')
    report='"Transaction Report since Sep 30, 2026 through Oct 31, 2026"\n"MTD Settled PL, USD:",-9.00\n"MTD fee, USD:",0.00\n"Account (USD): 3607739, Base currency: USD"\n'
    for role,text in ((m.HISTORY,history),(m.REPORT,report)):
        repo.upload(c.selected.id,role,role+'.csv','text/csv',text.encode())
    raw=export(EXEC,True).replace('Account Statement for synthetic','Account Statement for 12345678').encode()
    c.cash=RestoredUpload('cash.csv','text/csv',raw);c.supp=RestoredUpload('supp.csv','text/csv',raw)
    return c

def rev(c):return c.repositories[c.account].get(c.selected.id).revision
def review(c):return c.associated.review_forex_cash_sources(c.admin,c.account,c.selected.id,rev(c),c.cash,c.supp)
def save(c,**kwargs):
    opts=dict(reviewed_preview_sha256=review(c)['review_sha256'],confirm_same_account=True,confirm_source_choices=True)
    opts.update(kwargs)
    return c.associated.save_forex_cash_sources(c.admin,c.account,c.selected.id,rev(c),c.cash,c.supp,**opts)
def saved(c):return c.associated.forex_cash_source_preview(c.admin,c.account,c.selected.id,rev(c))

def test_receipt_saves_exact_originals_actor_and_repeat_changes_nothing(sources):
    c=sources;repo=c.repositories[c.account];before=deepcopy(repo.docs);revision=rev(c)
    assert save(c)==revision+3
    r=saved(c);receipt=json.loads(repo.document(c.selected.id,m.RECEIPT)[2])
    assert receipt['confirmed_by']==c.directory.user(c.admin).id
    assert receipt['publication_authorized'] is False and receipt['broker_verified_account_link'] is False
    assert '12345678' not in json.dumps(receipt) and '3607739' not in json.dumps(receipt)
    assert repo.document(c.selected.id,m.CASH)[2]==c.cash.getvalue()
    assert repo.document(c.selected.id,m.SUPPLEMENT)[2]==c.supp.getvalue()
    assert all(repo.docs[k]==v for k,v in before.items())
    original=deepcopy(repo.__dict__);assert save(c)==rev(c);saved(c)
    assert repo.__dict__==original and r['source_receipt_verified'] is True

@pytest.mark.parametrize('case',['stale','wrong_review','foreign','published','cash','supplement','receipt','postwrite'])
def test_failure_rolls_back_all_source_versions(sources,monkeypatch,case):
    c=sources;repo=c.repositories[c.account];before=deepcopy(repo.__dict__)
    digest=review(c)['review_sha256'];revision=rev(c);user=c.admin
    if case=='stale':revision-=1
    elif case=='wrong_review':digest='0'*64
    elif case=='foreign':user=c.member
    elif case=='published':monkeypatch.setattr(c.artifacts[c.account],'list_keys',lambda **k:('published',))
    else:
        real=repo.upload
        def upload(draft,role,*args,**kwargs):
            if role=={'cash':m.CASH,'supplement':m.SUPPLEMENT,'receipt':m.RECEIPT}.get(case):raise ValueError('injected source failure')
            value=real(draft,role,*args,**kwargs)
            if case=='postwrite' and role==m.RECEIPT:repo.docs[draft,m.CASH]=('changed.csv','text/csv',b'changed')
            return value
        monkeypatch.setattr(repo,'upload',upload)
    with pytest.raises((ValueError,PermissionError)):
        c.associated.save_forex_cash_sources(user,c.account,c.selected.id,revision,c.cash,c.supp,
            reviewed_preview_sha256=digest,confirm_same_account=True,confirm_source_choices=True)
    current=c.repositories[c.account]
    assert current.rows==before['rows'] and current.docs==before['docs'] and current.originals==before['originals']

@pytest.mark.parametrize('field,value',[('account_id','foreign'),('draft_id','foreign'),('policy_id','wrong'),
    ('source_choices_accepted',False),('publication_authorized',True),('broker_verified_account_link',True),
    ('confirmed_source_revision',True),('review_sha256','bad'),('confirmed_at','2026-01-01'),('version',True)])
def test_corrupt_receipt_is_not_adopted_or_repaired(sources,field,value):
    c=sources;save(c);repo=c.repositories[c.account]
    name,media,raw=repo.document(c.selected.id,m.RECEIPT);r=json.loads(raw);r[field]=value
    repo.docs[c.selected.id,m.RECEIPT]=(name,media,json.dumps(r).encode());before=deepcopy(repo.__dict__)
    with pytest.raises(ValueError):saved(c)
    with pytest.raises(ValueError):save(c,replace=True)
    current=c.repositories[c.account];assert current.docs==before['docs']

@pytest.mark.parametrize('role',[m.CASH,m.SUPPLEMENT,m.HISTORY,m.REPORT])
def test_changed_original_invalidates_saved_review(sources,role):
    c=sources;save(c);repo=c.repositories[c.account];name,media,raw=repo.document(c.selected.id,role)
    repo.upload(c.selected.id,role,name,media,raw+b'\n',replace=True)
    before=deepcopy(repo.__dict__)
    with pytest.raises(ValueError):saved(c)
    assert repo.__dict__==before


def test_replacement_requires_confirmation_preserves_all_originals(sources):
    c=sources;save(c);repo=c.repositories[c.account];old=c.cash.getvalue()
    c.cash=RestoredUpload('new.csv','text/csv',old+b'\n')
    with pytest.raises(ValueError,match='replacement'):save(c)
    save(c,replace=True);repo=c.repositories[c.account];assert any(row[2]==old for row in repo.originals)
    assert saved(c)['source_receipt_verified'] is True

@pytest.mark.parametrize('kwargs',[{'confirm_same_account':False},{'confirm_source_choices':1}, {'replace':'yes'},{'reviewed_preview_sha256':'bad'}])
def test_explicit_choices_required_before_connection(kwargs):
    from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
    obj=object.__new__(DeploymentMonthlyInputs)
    options=dict(reviewed_preview_sha256='a'*64,confirm_same_account=True,confirm_source_choices=True);options.update(kwargs)
    with pytest.raises(ValueError):obj.save_forex_cash_sources(None,'a','d',0,None,None,**options)
