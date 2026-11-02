from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from campaigniq import account_futures_preview as module
from campaigniq.account_administration import Permission
from campaigniq.import_draft_bridge import RestoredUpload
from tests.test_account_futures_opening import opening, saving, context, bound_context, confirm
from tests.test_futures_accounting import statement

PDF=b'%PDF-synthetic-futures-statement'


def pdf_text():return statement()+'\nGMI ACCOUNT NUMBER: SYNTHETIC482\n'


@pytest.fixture
def connected(opening,monkeypatch):
    c=opening;confirm(c)
    def extract(source,destination):
        destination.write_text(pdf_text());return destination
    monkeypatch.setattr(module,'extract_pdf_text',extract)
    return c


def save(c,content=PDF,**kwargs):
    repo=c.repositories[c.account]
    return c.associated.save_futures_statement(c.admin,c.account,c.selected.id,repo.get(c.selected.id).revision,
        RestoredUpload('monthly.pdf','application/pdf',content),confirm_same_account=True,**kwargs)


def preview(c,user=None):
    return c.associated.futures_preview(user or c.admin,c.account,c.selected.id,c.repositories[c.account].get(c.selected.id).revision)


def test_saved_exact_pdf_and_confirmation_feed_readonly_account_preview(connected):
    c=connected;repo=c.repositories[c.account];before=deepcopy(repo.docs)
    assert save(c)==5
    assert repo.document(c.selected.id,module.PDF_ROLE)[2]==PDF
    result=preview(c)
    assert result['account_id']==c.account and result['draft_id']==str(c.selected.id)
    assert float(result['realized_net_usd'])==-816
    assert result['opening_positions_confirmed_by_owner'] is True
    assert result['opening_positions_verified'] is False
    assert result['statement_account_link_broker_verified'] is False
    assert result['publication_authorized'] is result['production_integration_enabled'] is False
    assert result['statement_account_association']=='user_confirmed'
    assert all(repo.docs[k]==v for k,v in before.items())
    after=deepcopy(repo.__dict__);preview(c)
    assert repo.__dict__==after
    record=json.loads(repo.document(c.selected.id,module.RECEIPT_ROLE)[2])
    assert record['confirmed_by']==c.directory.user(c.admin).id
    assert 'SYNTHETIC482' not in json.dumps(record)
    assert '12345678' not in json.dumps(record)


def test_exact_repeat_does_not_touch_original_or_revision(connected):
    c=connected;save(c);repo=c.repositories[c.account];before=deepcopy(repo.__dict__)
    assert save(c)==5 and repo.__dict__==before


@pytest.mark.parametrize('choice',[False,None,1,'yes'])
def test_confirmation_required_before_connection(choice):
    from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
    service=object.__new__(DeploymentMonthlyInputs)
    with pytest.raises(ValueError):service.save_futures_statement(None,'a','d',0,None,confirm_same_account=choice)


@pytest.mark.parametrize('revision',[True,-1,None,'0'])
def test_invalid_revision_precedes_connections(revision):
    from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
    service=object.__new__(DeploymentMonthlyInputs)
    with pytest.raises(ValueError):service.save_futures_statement(None,'a','d',revision,None,confirm_same_account=True)
    with pytest.raises(ValueError):service.futures_preview(None,'a','d',revision)


@pytest.mark.parametrize('case',['stale','foreign','published','receipt_write','original_write','post_write'])
def test_failed_save_rolls_back_both_documents(connected,monkeypatch,case):
    c=connected;repo=c.repositories[c.account];before=deepcopy(repo.__dict__)
    user=c.admin;revision=3
    if case=='stale':revision=2
    elif case=='foreign':user=c.member
    elif case=='published':monkeypatch.setattr(c.artifacts[c.account],'list_keys',lambda **k:('published',))
    else:
        actual=repo.upload
        def upload(draft,role,*args,**kwargs):
            if (case=='receipt_write' and role==module.RECEIPT_ROLE) or (case=='original_write' and role==module.PDF_ROLE):
                raise ValueError('Injected storage failure')
            result=actual(draft,role,*args,**kwargs)
            if case=='post_write' and role==module.RECEIPT_ROLE:
                repo.docs[draft,module.PDF_ROLE]=('bad.pdf','application/pdf',b'%PDF-corrupt')
            return result
        monkeypatch.setattr(repo,'upload',upload)
    with pytest.raises((ValueError,PermissionError)):
        c.associated.save_futures_statement(user,c.account,c.selected.id,revision,
            RestoredUpload('monthly.pdf','application/pdf',PDF),confirm_same_account=True)
    current=c.repositories[c.account]
    assert current.rows==before['rows'] and current.docs==before['docs'] and current.originals==before['originals']


@pytest.mark.parametrize('case',['history','opening','binding','pdf'])
def test_changed_sources_withhold_entire_preview(connected,case):
    c=connected;save(c);repo=c.repositories[c.account]
    if case=='history':
        repo.upload(c.selected.id,'thinkorswim_trade_history','new.csv','text/csv',c.history+b'\n',replace=True)
    elif case=='opening':
        from campaigniq.account_futures_opening import ROLE
        record=json.loads(repo.document(c.selected.id,ROLE)[2]);record['confirmed_at']='2026-11-02T00:00:00+00:00'
        repo.upload(c.selected.id,ROLE,'changed.json','text/plain',json.dumps(record).encode(),replace=True)
    elif case=='binding':
        from campaigniq.broker_account_binding import bind
        c.directory=bind(c.directory,c.admin,c.account,'securities','87654321',expected_revision=1,confirm_replace=True)
    else:repo.upload(c.selected.id,module.PDF_ROLE,'changed.pdf','application/pdf',PDF+b'changed',replace=True)
    before=deepcopy(repo.docs)
    with pytest.raises(ValueError):preview(c)
    assert repo.docs==before


def test_replacement_requires_choice_and_preserves_prior_pdf(connected):
    c=connected;save(c);repo=c.repositories[c.account];before=deepcopy(repo.docs)
    with pytest.raises(ValueError,match='replacement'):save(c,PDF+b'new')
    assert repo.docs==before
    assert save(c,PDF+b'new',replace=True)==7
    repo=c.repositories[c.account]
    assert any(row[2]==PDF for row in repo.originals)
    assert preview(c)['statement_sha256']==module.digest(PDF+b'new')


def test_same_pdf_can_be_reassociated_after_explicit_new_opening_confirmation(connected):
    from campaigniq.account_futures_opening import ROLE
    c=connected;save(c);repo=c.repositories[c.account]
    record=json.loads(repo.document(c.selected.id,ROLE)[2]);record['confirmed_at']='2026-11-02T00:00:00+00:00'
    repo.upload(c.selected.id,ROLE,'changed.json','text/plain',json.dumps(record).encode(),replace=True)
    with pytest.raises(ValueError):preview(c)
    assert save(c)==7
    assert len([r for r in repo.originals if r[1]=='application/pdf'])==0
    assert preview(c)['opening_positions_confirmed_by_owner']


@pytest.mark.parametrize('value',['', 'GMI ACCOUNT NUMBER: ****482', 'GMI ACCOUNT NUMBER: OTHER482\nGMI ACCOUNT NUMBER: DIFFERENT482'])
def test_missing_masked_or_ambiguous_gmi_is_rejected(value):
    with pytest.raises(ValueError):module.gmi_digest(value)


def test_gmi_repeat_is_consistent_but_changed_account_replacement_blocked(connected,monkeypatch):
    c=connected;save(c);repo=c.repositories[c.account];before=deepcopy(repo.docs)
    def extract(source,destination):
        destination.write_text(pdf_text().replace('SYNTHETIC482','DIFFERENT482'));return destination
    monkeypatch.setattr(module,'extract_pdf_text',extract)
    with pytest.raises(ValueError,match='GMI account changed'):save(c,PDF+b'other',replace=True)
    assert repo.docs==before


@pytest.mark.parametrize('field,value',[('account_id','foreign'),('draft_id','foreign'),('broker_verified_account_link',True),
    ('evidence_type','broker_verified'),('statement_sha256','bad'),('opening_confirmation_sha256','bad'),
    ('confirmed_source_revision',True),('statement_date','2026-09-30')])
def test_corrupt_association_cannot_be_read_or_overwritten(connected,field,value):
    c=connected;save(c);repo=c.repositories[c.account]
    record=json.loads(repo.document(c.selected.id,module.RECEIPT_ROLE)[2]);record[field]=value
    repo.docs[c.selected.id,module.RECEIPT_ROLE]=('corrupt.json','text/plain',json.dumps(record).encode())
    before=dict(repo.docs)
    with pytest.raises(ValueError):preview(c)
    with pytest.raises(ValueError):save(c)
    assert repo.docs==before


def test_missing_confirmation_or_statement_does_not_adopt_an_orphan(connected):
    c=connected;repo=c.repositories[c.account]
    with pytest.raises(ValueError):preview(c)
    repo=c.repositories[c.account]
    repo.upload(c.selected.id,module.PDF_ROLE,'orphan.pdf','application/pdf',PDF)
    with pytest.raises(ValueError,match='differ'):save(c)


def test_viewers_can_read_but_not_associate_then_revocation_blocks_access(connected):
    c=connected;save(c);member=c.directory.user(c.member).id
    c.directory=c.directory.set_grant(c.admin,member,c.account,Permission.EDIT,expected_revision=c.directory.revision)
    assert preview(c,c.member)['statement_account_association']=='user_confirmed'
    with pytest.raises(PermissionError,match='owner'):
        c.associated.save_futures_statement(c.member,c.account,c.selected.id,5,
            RestoredUpload('monthly.pdf','application/pdf',PDF),confirm_same_account=True)
    c.directory=c.directory.set_grant(c.admin,member,c.account,None,expected_revision=c.directory.revision)
    with pytest.raises(PermissionError):preview(c,c.member)


def test_application_delegates_without_publication():
    from campaigniq.account_deployment_application import DeploymentMonthlyApplication
    calls=[]
    backend=SimpleNamespace(gateway=SimpleNamespace(_directory=None),
        save_futures_statement=lambda *a,**k:calls.append('save'),futures_preview=lambda *a,**k:calls.append('preview'))
    app=DeploymentMonthlyApplication(backend,None,publication_enabled=False)
    app.save_futures_statement('u','a','d',1,None,confirm_same_account=True)
    app.futures_preview('u','a','d',3)
    assert calls==['save','preview'] and not app.publication_enabled


@pytest.mark.parametrize('case',['missing_opening','wrong_month','bad_cash','bad_quantity'])
def test_invalid_source_evidence_creates_no_pdf_or_receipt(connected,monkeypatch,case):
    c=connected;repo=c.repositories[c.account]
    if case=='missing_opening':
        from campaigniq.account_futures_opening import ROLE
        del repo.docs[c.selected.id,ROLE]
    else:
        def extract(source,destination):
            text=pdf_text()
            if case=='wrong_month':text=text.replace('OCT 30, 2026','SEP 30, 2026')
            elif case=='bad_cash':text=text.replace('184.00','185.00')
            else:text=text.replace('110','111')
            destination.write_text(text);return destination
        monkeypatch.setattr(module,'extract_pdf_text',extract)
    before=deepcopy(repo.docs)
    with pytest.raises(ValueError):save(c)
    assert c.repositories[c.account].docs==before


def test_archived_account_keeps_preview_but_cannot_replace_statement(connected):
    c=connected;save(c)
    c.directory=c.directory.configure_account(c.admin,c.account,archived=True,expected_revision=c.directory.revision)
    assert preview(c)['publication_authorized'] is False
    with pytest.raises(PermissionError):save(c,PDF+b'new',replace=True)


def test_disabled_grantee_cannot_read_futures_financials(connected):
    c=connected;save(c);member=c.directory.user(c.member).id
    c.directory=c.directory.set_grant(c.admin,member,c.account,Permission.VIEW,expected_revision=c.directory.revision)
    c.directory=c.directory.configure_user(c.admin,member,enabled=False,expected_revision=c.directory.revision)
    with pytest.raises(PermissionError):preview(c,c.member)


def test_preview_rejects_stale_revision(connected):
    c=connected;save(c)
    with pytest.raises(ValueError,match='Draft changed'):
        c.associated.futures_preview(c.admin,c.account,c.selected.id,4)
