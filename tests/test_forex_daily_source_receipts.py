from copy import deepcopy
import io
import json
import zipfile
import pytest
from campaigniq import account_forex_daily_sources as daily
from campaigniq.forex_daily_rehearsal_sources import upload
from campaigniq.import_draft_bridge import RestoredUpload
from tests.test_account_forex_cash_receipts import sources, saving, bound_context, context, save as save_cash


@pytest.fixture
def confirmed(sources, monkeypatch):
    c=sources;save_cash(c);c.daily=upload('3607739')
    monkeypatch.setattr(daily,'current_roles',lambda session,draft:{role for (did,role) in session.drafts.docs if did==draft.id and role.startswith(daily.PREFIX)})
    monkeypatch.setattr(daily,'current_owner',lambda service,session:c.directory.user(c.admin).id)
    return c

def repo(c):return c.repositories[c.account]
def revision(c):return repo(c).get(c.selected.id).revision
def review(c):return c.associated.forex_daily_inventory_preview(c.admin,c.account,c.selected.id,revision(c),c.daily)
def save(c,**kwargs):
    options=dict(reviewed_preview_sha256=review(c)['review_sha256'],confirm_same_account=True,confirm_coverage_limits=True)
    options.update(kwargs)
    return c.associated.save_forex_daily_sources(c.admin,c.account,c.selected.id,revision(c),c.daily,**options)
def saved(c):return c.associated.forex_daily_source_preview(c.admin,c.account,c.selected.id,revision(c))

def changed_archive(original):
    output=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original.content)) as src,zipfile.ZipFile(output,'w') as dst:
        for i,item in enumerate(src.infolist()):dst.writestr(item,src.read(item)+(b'\n' if i==0 else b''))
    return RestoredUpload(original.name,original.media_type,output.getvalue())


def test_exact_daily_originals_owner_attribution_and_repeat_noop(confirmed):
    c=confirmed;before=deepcopy(repo(c).docs);first=revision(c)
    assert save(c)==first+6
    result=saved(c);receipt=json.loads(repo(c).document(c.selected.id,daily.RECEIPT)[2])
    assert receipt['confirmed_by']==c.directory.user(c.admin).id
    assert receipt['publication_authorized'] is False and receipt['archive_bytes_saved'] is False
    assert result['daily_pdfs_saved'] and result['source_receipt_verified']
    assert result['full_independent_daily_coverage'] is False
    from campaigniq.account_forex_daily_inventory import originals
    originals_by_hash={daily.digest(raw):raw for name,raw in originals(c.daily.content)}
    for item in receipt['pdf_inventory']:
        assert repo(c).document(c.selected.id,item['role'])[2]==originals_by_hash[item['pdf_sha256']]
    assert all(repo(c).docs[key]==value for key,value in before.items())
    state=deepcopy(repo(c).__dict__);assert save(c)==revision(c);saved(c)
    assert repo(c).__dict__==state
    assert '3607739' not in json.dumps(receipt)


@pytest.mark.parametrize('failure',['first_pdf','last_pdf','receipt','postwrite'])
def test_all_pdf_receipt_and_revision_writes_roll_back(confirmed,monkeypatch,failure):
    c=confirmed;store=repo(c);before=deepcopy(store.__dict__);real=store.upload
    def fail(did,role,*args,**kwargs):
        value=real(did,role,*args,**kwargs)
        if ((failure=='first_pdf' and role==daily.PREFIX+'2026-10-01') or
            (failure=='last_pdf' and role==daily.PREFIX+'2026-10-07') or
            (failure=='receipt' and role==daily.RECEIPT)):raise RuntimeError('Injected daily save failure')
        if failure=='postwrite' and role==daily.RECEIPT:
            name,media,raw=store.docs[did,daily.PREFIX+'2026-10-01']
            store.docs[did,daily.PREFIX+'2026-10-01']=(name,media,raw+b'changed')
        return value
    monkeypatch.setattr(store,'upload',fail)
    with pytest.raises((RuntimeError,ValueError)):save(c)
    assert repo(c).rows==before['rows'] and repo(c).docs==before['docs'] and repo(c).originals==before['originals']


@pytest.mark.parametrize('field,value',[('account_id','foreign'),('policy','wrong'),('version',True),
    ('publication_authorized',True),('broker_verified_account_link',True),('coverage_limits_accepted',False),
    ('confirmed_source_revision',True),('calculation_sha256','0'*64),('archive_sha256','0'*64),
    ('confirmed_at','2026-10-01'),('confirmed_by','foreign-owner'),('binding_sha256',{}),('pdf_inventory',[])])
def test_corrupt_receipt_is_not_adopted_or_repaired(confirmed,field,value):
    c=confirmed;save(c);name,media,raw=repo(c).document(c.selected.id,daily.RECEIPT)
    body=json.loads(raw);body[field]=value;repo(c).docs[c.selected.id,daily.RECEIPT]=(name,media,json.dumps(body).encode())
    before=deepcopy(repo(c).docs)
    with pytest.raises(ValueError):saved(c)
    with pytest.raises(ValueError):save(c,replace=True)
    assert repo(c).docs==before


def test_replacement_is_explicit_and_preserves_old_pdf_and_receipt(confirmed):
    c=confirmed;save(c);original_receipt=repo(c).document(c.selected.id,daily.RECEIPT)[2]
    old=repo(c).document(c.selected.id,daily.PREFIX+'2026-10-01')[2];before=revision(c)
    c.daily=changed_archive(c.daily)
    with pytest.raises(ValueError,match='replacement'):save(c)
    assert save(c,replace=True)==before+2
    assert saved(c)['source_receipt_verified']
    assert any(row[2]==old for row in repo(c).originals)
    assert any(row[2]==original_receipt for row in repo(c).originals)


def test_additional_daily_dates_allowed_but_removing_dates_requires_separate_review(confirmed):
    c=confirmed;save(c);c.daily=upload('3607739',complete=True)
    with pytest.raises(ValueError,match='replacement'):save(c)
    save(c,replace=True);assert saved(c)['full_independent_daily_coverage']
    c.daily=upload('3607739')
    with pytest.raises(ValueError,match='remove previously'):save(c,replace=True)


@pytest.mark.parametrize('case',['missing','extra','changed','orphan'])
def test_unreceipted_or_changed_pdf_originals_fail_without_adoption(confirmed,case):
    c=confirmed
    if case!='orphan':save(c)
    role=daily.PREFIX+'2026-10-01'
    if case=='missing':del repo(c).docs[c.selected.id,role]
    elif case=='extra' or case=='orphan':repo(c).docs[c.selected.id,daily.PREFIX+'2026-10-28']=('unexpected.pdf','application/pdf',b'%PDF-unexpected')
    else:
        name,media,raw=repo(c).document(c.selected.id,role);repo(c).docs[c.selected.id,role]=(name,media,raw+b'\n')
    before=deepcopy(repo(c).docs)
    with pytest.raises(ValueError):saved(c)
    with pytest.raises(ValueError):save(c,replace=True)
    assert repo(c).docs==before


@pytest.mark.parametrize('kwargs',[{'confirm_same_account':False},{'confirm_coverage_limits':1},
    {'reviewed_preview_sha256':'bad'},{'replace':'yes'}])
def test_explicit_choices_checked_before_connection(kwargs):
    from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
    obj=object.__new__(DeploymentMonthlyInputs)
    options=dict(reviewed_preview_sha256='a'*64,confirm_same_account=True,confirm_coverage_limits=True);options.update(kwargs)
    with pytest.raises(ValueError):obj.save_forex_daily_sources(None,'a','d',0,None,**options)


def test_foreign_stale_wrong_review_and_published_save_reject_without_writes(confirmed,monkeypatch):
    c=confirmed;rev=revision(c);digest=review(c)['review_sha256'];before=deepcopy(repo(c).__dict__)
    for user,version,proof in ((c.member,rev,digest),(c.admin,rev-1,digest),(c.admin,rev,'0'*64)):
        with pytest.raises((PermissionError,ValueError)):
            c.associated.save_forex_daily_sources(user,c.account,c.selected.id,version,c.daily,
                reviewed_preview_sha256=proof,confirm_same_account=True,confirm_coverage_limits=True)
    monkeypatch.setattr(c.artifacts[c.account],'list_keys',lambda **kwargs:('published',))
    with pytest.raises(ValueError,match='Published'):save(c)
    assert repo(c).rows==before['rows'] and repo(c).docs==before['docs']


def test_application_delegates_without_publisher():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from campaigniq.account_deployment_application import DeploymentMonthlyApplication
    app=object.__new__(DeploymentMonthlyApplication);app._inputs=SimpleNamespace(save_forex_daily_sources=Mock(return_value=9),forex_daily_source_preview=Mock(return_value='saved'))
    assert app.save_forex_daily_sources('u','a','d',1,'zip')==9
    assert app.forex_daily_source_preview('u','a','d',9)=='saved'
