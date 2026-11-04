from copy import deepcopy
from types import SimpleNamespace
import json
import pytest

from campaigniq import account_crypto_statement_sources as module
from campaigniq.account_administration import Permission
from campaigniq.import_draft_bridge import RestoredUpload
from tests.test_gain_loss_association import saving,bound_context,context
from tests.test_crypto_support import EXPORT
from tests.test_optional_markets_and_crypto_statement import STATEMENT


def pdf(text):
    commands=['BT /F1 7 Tf 9 TL 10 780 Td']
    for i,line in enumerate(text.splitlines()):
        escaped=line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
        commands.append(('T* ' if i else '')+'('+escaped+') Tj')
    stream=('\n'.join([*commands,'ET'])).encode()
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Count 1 /Kids [3 0 R] >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 900 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>',
        b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    raw=b'%PDF-1.4\n';offsets=[]
    for i,obj in enumerate(objects,1):
        offsets.append(len(raw));raw+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    xref=len(raw);raw+=b'xref\n0 6\n0000000000 65535 f \n'
    for offset in offsets:raw+=f'{offset:010d} 00000 n \n'.encode()
    return raw+b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF\n'


@pytest.fixture
def crypto(saving):
    c=saving
    raw=EXPORT.replace('Account Statement for synthetic account since 8/31/26 through 9/29/26',
        'Account Statement for 12345678 since 9/30/26 through 10/31/26').replace('SYNTHETIC','TEST000841')
    raw=raw.replace('9/28/26','10/28/26').replace('9/27/26','10/27/26').replace('9/29/26','10/31/26')
    repo=c.repositories[c.account]
    repo.upload(c.selected.id,'thinkorswim_trade_history','history.csv','text/csv',raw.encode())
    previous='''Schwab CryptoTM Account of
****-******0841 September 1-30, 2026
CASH 0.00 0.00
Position Details - Crypto 1
Transaction Details
Disclosures
'''
    current=STATEMENT.replace('September 1-30','October 1-31').replace('09/28','10/28')
    c.previous=RestoredUpload('previous.pdf','application/pdf',pdf(previous))
    c.current=RestoredUpload('current.pdf','application/pdf',pdf(current))
    return c


def rev(c):return c.repositories[c.account].get(c.selected.id).revision
def review(c):return c.associated.review_crypto_statements(c.admin,c.account,c.selected.id,rev(c),c.previous,c.current)
def save(c,**kwargs):
    options=dict(reviewed_preview_sha256=review(c)['reviewed_preview_sha256'],confirm_same_crypto_account=True,confirm_source_choices=True)
    options.update(kwargs)
    return c.associated.save_crypto_statement_sources(c.admin,c.account,c.selected.id,rev(c),c.previous,c.current,**options)
def preview(c,user=None):return c.associated.crypto_statement_preview(user or c.admin,c.account,c.selected.id,rev(c))


def test_exact_pdf_originals_and_source_choices_save_atomically_and_read_without_writes(crypto):
    c=crypto;repo=c.repositories[c.account];before=deepcopy(repo.docs);initial=rev(c)
    reviewed=review(c)
    assert repo.docs==before and reviewed['publication_authorized'] is False
    assert save(c)==initial+3
    assert repo.document(c.selected.id,module.PREVIOUS)[2]==c.previous.content
    assert repo.document(c.selected.id,module.CURRENT)[2]==c.current.content
    result=preview(c)
    assert result['source_receipt_verified'] and result['publication_authorized'] is False
    assert len(result['calculation']['source_choice_rows'])==3
    assert result['calculation']['totals']['full_ending_cost_basis_usd']=='500.00'
    receipt=json.loads(repo.document(c.selected.id,module.RECEIPT)[2])
    assert receipt['confirmed_by']==c.directory.user(c.admin).id and receipt['source_choices_accepted'] is True
    assert receipt['broker_verified_account_link'] is False
    assert 'TEST000841' not in json.dumps(receipt) and '12345678' not in json.dumps(receipt)
    after=deepcopy(repo.__dict__);assert save(c)==rev(c) and repo.__dict__==after
    preview(c);assert repo.__dict__==after
    assert all(repo.docs[k]==v for k,v in before.items())


@pytest.mark.parametrize('kwargs',[{'confirm_same_crypto_account':False},{'confirm_source_choices':False},
    {'confirm_source_choices':1},{'replace':'yes'},{'reviewed_preview_sha256':'bad'}])
def test_required_explicit_choices_fail_before_connection(kwargs):
    from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
    service=object.__new__(DeploymentMonthlyInputs)
    defaults=dict(reviewed_preview_sha256='a'*64,confirm_same_crypto_account=True,confirm_source_choices=True)
    defaults.update(kwargs)
    with pytest.raises(ValueError):service.save_crypto_statement_sources(None,'account','draft',0,None,None,**defaults)


def test_missing_pdf_is_a_validation_failure_before_any_account_connection():
    from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
    service=object.__new__(DeploymentMonthlyInputs)
    with pytest.raises(ValueError,match='PDF'):
        service.review_crypto_statements(None,'account','draft',0,None,None)


@pytest.mark.parametrize('case',['stale','wrong_review','foreign','published','previous_write','current_write','receipt_write','post_write'])
def test_rejected_or_failed_saves_roll_back_both_pdfs_and_receipt(crypto,monkeypatch,case):
    c=crypto;repo=c.repositories[c.account];before=deepcopy(repo.__dict__)
    displayed=review(c)['reviewed_preview_sha256'];revision=rev(c);actor=c.admin
    if case=='stale':revision-=1
    elif case=='wrong_review':displayed='0'*64
    elif case=='foreign':actor=c.member
    elif case=='published':monkeypatch.setattr(c.artifacts[c.account],'list_keys',lambda **k:('published',))
    else:
        real=repo.upload
        def upload(draft,role,*args,**kwargs):
            fail={'previous_write':module.PREVIOUS,'current_write':module.CURRENT,'receipt_write':module.RECEIPT}
            if role==fail.get(case):raise ValueError('injected source write failure')
            result=real(draft,role,*args,**kwargs)
            if case=='post_write' and role==module.RECEIPT:repo.docs[draft,module.CURRENT]=('bad.pdf','application/pdf',b'%PDF-corrupt')
            return result
        monkeypatch.setattr(repo,'upload',upload)
    with pytest.raises((ValueError,PermissionError)):
        c.associated.save_crypto_statement_sources(actor,c.account,c.selected.id,revision,c.previous,c.current,
            reviewed_preview_sha256=displayed,confirm_same_crypto_account=True,confirm_source_choices=True)
    current=c.repositories[c.account]
    assert current.rows==before['rows'] and current.docs==before['docs'] and current.originals==before['originals']


@pytest.mark.parametrize('field,value',[('account_id','foreign'),('draft_id','foreign'),('source_choices_accepted',False),
    ('broker_verified_account_link',True),('confirmed_source_revision',True),('policy_id','different'),('preview_sha256','bad')])
def test_corrupt_receipt_blocks_reads_and_cannot_be_overwritten(crypto,field,value):
    c=crypto;save(c);repo=c.repositories[c.account]
    body=json.loads(repo.document(c.selected.id,module.RECEIPT)[2]);body[field]=value
    repo.docs[c.selected.id,module.RECEIPT]=('bad','text/plain',json.dumps(body).encode());before=deepcopy(repo.docs)
    with pytest.raises(ValueError):preview(c)
    with pytest.raises(ValueError):save(c,replace=True)
    assert c.repositories[c.account].docs==before


def test_source_changes_require_fresh_review_and_explicit_replacement_preserving_originals(crypto):
    c=crypto;save(c);repo=c.repositories[c.account];old=repo.document(c.selected.id,module.CURRENT)
    prior_receipt=repo.document(c.selected.id,module.RECEIPT)
    c.current=RestoredUpload('new.pdf','application/pdf',c.current.content+b'\n')
    with pytest.raises(ValueError,match='replacement'):save(c)
    save(c,replace=True)
    repo=c.repositories[c.account]
    assert old in repo.originals and prior_receipt in repo.originals
    assert preview(c)['source_receipt_verified']
    raw=repo.document(c.selected.id,module.HISTORY)
    repo.upload(c.selected.id,module.HISTORY,'changed.csv','text/csv',raw[2]+b'\n',replace=True)
    with pytest.raises(ValueError,match='changed'):preview(c)
    save(c,replace=True)
    assert preview(c)['source_receipt_verified']


def test_crypto_identifier_or_full_broker_binding_conflict_cannot_be_adopted(crypto):
    c=crypto;save(c);repo=c.repositories[c.account]
    raw=repo.document(c.selected.id,module.HISTORY)[2]
    repo.upload(c.selected.id,module.HISTORY,'changed.csv','text/csv',raw.replace(b'TEST000841',b'DIFF000841'),replace=True)
    before=deepcopy(repo.docs)
    with pytest.raises(ValueError,match='identity changed'):save(c,replace=True)
    assert c.repositories[c.account].docs==before


def test_view_grantee_can_read_saved_evidence_but_cannot_save_owner_choices(crypto):
    c=crypto;save(c)
    c.directory=c.directory.set_grant(c.admin,c.directory.user(c.member).id,c.account,Permission.EDIT,expected_revision=c.directory.revision)
    assert preview(c,c.member)['source_receipt_verified']
    with pytest.raises(PermissionError,match='owner'):
        c.associated.save_crypto_statement_sources(c.member,c.account,c.selected.id,rev(c),c.previous,c.current,
            reviewed_preview_sha256=review(c)['reviewed_preview_sha256'],confirm_same_crypto_account=True,confirm_source_choices=True)


def test_application_delegates_without_enabling_publication():
    from campaigniq.account_deployment_application import DeploymentMonthlyApplication
    calls=[]
    service=SimpleNamespace(gateway=SimpleNamespace(_directory=None),review_crypto_statements=lambda *a,**k:calls.append('review'),
        save_crypto_statement_sources=lambda *a,**k:calls.append('save'),crypto_statement_preview=lambda *a,**k:calls.append('preview'))
    app=DeploymentMonthlyApplication(service,None,publication_enabled=False)
    app.review_crypto_statements();app.save_crypto_statement_sources();app.crypto_statement_preview()
    assert calls==['review','save','preview'] and app.publication_enabled is False


def test_existing_unapproved_current_input_is_revalidated_without_duplicate_pdf(crypto):
    c=crypto;repo=c.repositories[c.account]
    repo.upload(c.selected.id,module.CURRENT,c.current.name,'application/pdf',c.current.content)
    initial=rev(c);assert save(c)==initial+2
    assert preview(c)['source_receipt_verified'] and not repo.originals


def test_orphan_previous_metadata_is_not_repaired(crypto):
    c=crypto;repo=c.repositories[c.account]
    repo.upload(c.selected.id,module.PREVIOUS,c.previous.name,'application/pdf',c.previous.content)
    before=deepcopy(repo.docs)
    with pytest.raises(ValueError):save(c,replace=True)
    assert c.repositories[c.account].docs==before


def test_duplicate_receipt_field_cannot_override_account_scope(crypto):
    c=crypto;save(c);repo=c.repositories[c.account]
    name,media,raw=repo.document(c.selected.id,module.RECEIPT)
    raw=raw.rstrip()[:-1]+b',"version":1}'
    repo.docs[c.selected.id,module.RECEIPT]=(name,media,raw);before=deepcopy(repo.docs)
    with pytest.raises(ValueError,match='Duplicate'):preview(c)
    with pytest.raises(ValueError,match='Duplicate'):save(c,replace=True)
    assert c.repositories[c.account].docs==before
