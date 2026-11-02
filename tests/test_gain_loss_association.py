import csv
import io
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import pytest
from campaigniq.account_gain_loss_association import ROLE,RECEIPT_ROLE,make_receipt,validate_receipt
from campaigniq.broker_account_binding import BrokerBinding
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.importers.schwab.realized_csv import HEADERS,read,period
from campaigniq.import_contract import MonthlyInputRole
from campaigniq.import_validation import validate_monthly_input
from tests.test_account_import_preview import Repository


def report(**changes):
    r=dict.fromkeys(HEADERS,'')
    r.update({'Symbol':'ABC 11/20/2026 100.00 C','Name':'Synthetic option','Closed Date':'10/15/2026','Opened Date':'09/01/2026','Quantity':'2','Proceeds Per Share':'$1.00','Cost Per Share':'$0.50','Proceeds':'$200.00','Cost Basis (CB)':'$100.00','Gain/Loss ($)':'$100.00','Term':'Short Term','Wash Sale?':'No'})
    r.update(changes);out=io.StringIO();w=csv.writer(out)
    w.writerow(['Realized Gain/Loss - Lot Details for My_Brokerage as of Mon Nov 02 07:01:42 EDT 2026 from 10/01/2026 to 10/31/2026']+['']*24)
    w.writerow(HEADERS);w.writerow([r[k] for k in HEADERS]);return out.getvalue()


def statement(number='1234-5678',month='October'):
    return f'Account Number\n{number}\n{month} 1-31, 2026\nPositions - Equities\nABC Company 10.0000 100.0000 1,000.00 1,000.00 0.00\nTotal Equities\n'


def test_csv_exact_values_and_period(tmp_path):
    text=report();r=read(text)[0]
    assert period(text)==(date(2026,10,1),date(2026,10,31))
    assert (r.quantity,r.proceeds,r.cost_basis,r.gain_loss,r.term)==(Decimal(2),Decimal(200),Decimal(100),Decimal(100),'SHORT')
    p=tmp_path/'r.csv';p.write_text(text)
    for month,expected in ((10,True),(9,False)):
        end=31 if month==10 else 30
        assert validate_monthly_input(MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS,p,period_start=date(2026,month,1),period_end=date(2026,month,end)).valid is expected

@pytest.mark.parametrize('change',[
    {'Gain/Loss ($)':'$99.00'},{'Quantity':'0'},{'Proceeds':'NaN'},
    {'Closed Date':'09/30/2026'},{'Opened Date':'11/01/2026'},
    {'Symbol':'/ES 11/20/2026 100 C'},{'Term':'Unknown'},{'Wash Sale?':'Maybe'},
    {'Cost Basis (CB)':'$1,00.00'},
])
def test_csv_bad_rows_not_silently_dropped(change):
    with pytest.raises(ValueError):read(report(**change))

def test_wash_sale_and_long_term_preserved():
    r=read(report(**{'Term':'Long Term','Wash Sale?':'Yes','Disallowed Loss':'$20.00','Gain/Loss ($)':'$120.00'}))[0]
    assert r.disallowed_loss==20 and r.term=='LONG' and r.expected_gain_loss==r.gain_loss

def test_csv_columns_and_truncation():
    with pytest.raises(ValueError):read(report().replace('Cost Basis (CB)','Wrong Column'))
    with pytest.raises(ValueError):read(report().rsplit(',',1)[0])

@pytest.fixture
def association(tmp_path):
    repo=Repository('private-account');draft=repo.create_or_resume(2026,10)
    session=SimpleNamespace(drafts=repo,account=SimpleNamespace(account_id='account-a'))
    known={'securities':BrokerBinding.create('securities','12345678')}
    service=SimpleNamespace(_known=lambda s:known,_path=lambda role,paths:paths[role])
    paths={};uploads={}
    for role,text in ((ROLE,report()),('schwab_brokerage_statement',statement())):
        paths[role]=tmp_path/(role+'.txt');paths[role].write_text(text)
        uploads[role]=RestoredUpload(role+'.txt','text/plain',text.encode())
    receipt=make_receipt(session,draft,'actor-a',known,paths[ROLE],paths['schwab_brokerage_statement'],uploads[ROLE].content,uploads['schwab_brokerage_statement'].content)
    repo.upload(draft.id,RECEIPT_ROLE,'confirmation.json','text/plain',json.dumps(receipt).encode())
    return SimpleNamespace(session=session,draft=draft,service=service,known=known,paths=paths,uploads=uploads,receipt=receipt)

def test_confirmation_roundtrip_preserves_originals(association):
    c=association;originals=dict(c.uploads)
    assert validate_receipt(c.service,c.session,c.draft,c.uploads,c.paths)==c.receipt
    assert c.uploads==originals and c.receipt['confirmed_by']=='actor-a'
    assert '12345678' not in json.dumps(c.receipt)

@pytest.mark.parametrize('change',['report','statement','binding','account','draft','month','corrupt'])
def test_changed_scope_or_source_invalidates(association,change):
    c=association
    if change in ('report','statement'):
        key=ROLE if change=='report' else 'schwab_brokerage_statement'
        c.uploads[key]=replace(c.uploads[key],content=c.uploads[key].content+b'\n')
    elif change=='binding':c.known['securities']=BrokerBinding.create('securities','87654321')
    elif change=='account':c.session.account.account_id='account-b'
    elif change=='draft':c.draft=replace(c.draft,id=__import__('uuid').uuid4());c.session.drafts.rows[c.draft.id]=c.draft
    elif change=='month':c.draft=replace(c.draft,month=9)
    else:c.session.drafts.docs[c.draft.id,RECEIPT_ROLE]=('bad','text/plain',b'{}')
    with pytest.raises((ValueError,FileNotFoundError)):validate_receipt(c.service,c.session,c.draft,c.uploads,c.paths)

def test_missing_confirmation_not_inferred(association):
    c=association;del c.session.drafts.docs[c.draft.id,RECEIPT_ROLE]
    with pytest.raises(ValueError,match='Confirm'):validate_receipt(c.service,c.session,c.draft,c.uploads,c.paths)

def test_wrong_statement_rejected(association):
    c=association
    for text in (statement('8765-4321'),statement(month='September')):
        c.paths['schwab_brokerage_statement'].write_text(text)
        with pytest.raises(ValueError):make_receipt(c.session,c.draft,'actor',c.known,c.paths[ROLE],c.paths['schwab_brokerage_statement'],b'r',b's')

def test_application_delegates_without_publication():
    from campaigniq.account_deployment_application import DeploymentMonthlyApplication
    calls=[]
    backend=SimpleNamespace(gateway=SimpleNamespace(_directory=None),save_associated_gain_loss=lambda *a,**k:calls.append((a,k)))
    app=DeploymentMonthlyApplication(backend,None,publication_enabled=False)
    app.save_associated_gain_loss('user','account','draft',1,'report',confirm_same_account=True)
    assert len(calls)==1 and app.publication_enabled is False

# Exercise the actual save implementation with a transaction-capable memory
# fixture. PostgreSQL integration remains a separate local rehearsal.
from tests.test_account_import_preview import context
from tests.test_broker_account_binding import bound_context
from tests.test_account_deployment_publication import deployed,local,month,FIXTURES

@pytest.fixture
def saving(bound_context,monkeypatch):
    from contextlib import contextmanager
    from copy import deepcopy
    from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
    import campaigniq.account_deployment_inputs as module
    from campaigniq.broker_account_binding import bind
    c=bound_context
    c.directory=bind(c.directory,c.admin,c.account,'securities','12345678',expected_revision=0)
    c.bound.gateway._directory.settings=object()
    service=object.__new__(DeploymentMonthlyInputs);service.gateway=c.bound.gateway
    service._lock=c.bound._lock;service._known=c.bound._known
    original=service.gateway.transaction
    @contextmanager
    def transaction(user,account,*,write=False):
        before=deepcopy(c.repositories)
        try:
            with original(user,account,write=write) as session:yield session
        except BaseException:
            c.repositories.clear();c.repositories.update(before);raise
    monkeypatch.setattr(service.gateway,'transaction',transaction)
    monkeypatch.setattr(module,'DirectoryTransaction',lambda *a:SimpleNamespace(_actor=lambda user:(c.directory,SimpleNamespace(identity=user,user_id=c.directory.user(user).id))))
    draft=service.create(c.admin,c.account,2026,10)
    service.save(c.admin,c.account,draft.id,0,'schwab_brokerage_statement',RestoredUpload('statement.txt','text/plain',statement().encode()))
    c.associated=service;c.selected=draft
    return c


def save_report(c,**kwargs):
    revision=c.repositories[c.account].get(c.selected.id).revision
    return c.associated.save_associated_gain_loss(c.admin,c.account,c.selected.id,revision,
        RestoredUpload('report.csv','text/csv',report().encode()),confirm_same_account=True,**kwargs)


def test_save_preserves_original_and_records_actor_in_same_transaction(saving):
    c=saving;revision=save_report(c);repo=c.repositories[c.account]
    assert revision==3
    assert repo.document(c.selected.id,ROLE)[2]==report().encode()
    record=json.loads(repo.document(c.selected.id,RECEIPT_ROLE)[2])
    assert record['confirmed_by']==c.directory.user(c.admin).id
    result=c.associated.preview(c.admin,c.account,c.selected.id,revision,forex_applicable=False,crypto_applicable=False)
    row=next(r for r in result.validations if r.role=='account:'+ROLE)
    assert row.valid and 'confirmed by user' in row.message
    assert not result.inputs_ready # Missing history is still required.


@pytest.mark.parametrize('failure',['confirmation','revision','foreign','receipt_write','published'])
def test_save_rejections_and_failure_leave_no_partial_document(saving,monkeypatch,failure):
    c=saving;repo=c.repositories[c.account];before=__import__('copy').deepcopy(c.repositories)
    args=[c.admin,c.account,c.selected.id,1,RestoredUpload('report.csv','text/csv',report().encode())]
    kwargs={'confirm_same_account':True}
    if failure=='confirmation':kwargs['confirm_same_account']=False
    elif failure=='revision':args[3]=0
    elif failure=='foreign':args[0]=c.member
    elif failure=='published':monkeypatch.setattr(c.artifacts[c.account],'list_keys',lambda **k:('published.json',))
    else:
        original=repo.upload
        def upload(draft,role,*a,**k):
            if role==RECEIPT_ROLE:raise ValueError('Injected receipt failure')
            return original(draft,role,*a,**k)
        monkeypatch.setattr(repo,'upload',upload)
    with pytest.raises((ValueError,PermissionError)):
        c.associated.save_associated_gain_loss(*args,**kwargs)
    current=c.repositories[c.account]
    assert current.rows==before[c.account].rows and current.docs==before[c.account].docs


def test_replacement_preserves_report_original_and_invalidates_old_confirmation(saving):
    c=saving;save_report(c);repo=c.repositories[c.account]
    revised=report(**{'Name':'Updated description'})
    with pytest.raises(ValueError):
        c.associated.save_associated_gain_loss(c.admin,c.account,c.selected.id,3,RestoredUpload('new.csv','text/csv',revised.encode()),confirm_same_account=True)
    revision=c.associated.save_associated_gain_loss(c.admin,c.account,c.selected.id,3,RestoredUpload('new.csv','text/csv',revised.encode()),confirm_same_account=True,replace=True)
    repo=c.repositories[c.account]
    assert any(row[2]==report().encode() for row in repo.originals)
    assert revision==5
    result=c.associated.preview(c.admin,c.account,c.selected.id,revision,forex_applicable=False,crypto_applicable=False)
    assert next(v for v in result.validations if v.role=='account:'+ROLE).valid


def test_publication_receipt_carries_confirmation_and_canonical_reader_detects_drift(deployed):
    from campaigniq.account_deployment_results import _deployment_results_session
    from campaigniq.monthly_upload_staging import stage_monthly_uploads
    from pathlib import Path
    from tempfile import TemporaryDirectory
    c=deployed;service=c.publisher.inputs;repo=c.repositories[c.account]
    # The existing synthetic January fixture has no realized closes. Its report
    # still has a real declared month and the confirmation is fingerprinted.
    csv_text=report().replace('10/01/2026 to 10/31/2026','01/01/2025 to 01/31/2025')
    csv_text='\n'.join(csv_text.splitlines()[:2])+'\n'
    repo.upload(c.draft.id,ROLE,'empty.csv','text/csv',csv_text.encode(),replace=True)
    from campaigniq.import_draft_bridge import restore_uploads
    with c.bound.gateway.transaction(c.admin,c.account,write=True) as session:
        draft=repo.get(c.draft.id);uploads=restore_uploads(repo,draft.id)
        with TemporaryDirectory() as d:
            supplied=stage_monthly_uploads(upload_dir=Path(d),uploads=uploads)
            receipt=make_receipt(session,draft,c.directory.user(c.admin).id,service._known(session),service._path(ROLE,supplied),service._path('schwab_brokerage_statement',supplied),uploads[ROLE].content,uploads['schwab_brokerage_statement'].content)
        revision=repo.upload(draft.id,RECEIPT_ROLE,'confirmation.json','text/plain',json.dumps(receipt).encode())
    c.publisher.publish(c.admin,c.account,c.draft.id,revision,confirm_publication=True,forex_applicable=False,crypto_applicable=False)
    stored=json.loads(c.artifacts[c.account].read_text('2025-01-account-publication-receipt.json'))
    assert stored['gain_loss_account_confirmation']==receipt
    with c.bound.gateway.transaction(c.admin,c.account) as session:
        result=_deployment_results_session(service,session,repo.get(c.draft.id),c.account,revision)
        assert result.positions==(('SPY','110.0000'),)
        receipt['confirmed_by']='changed-actor'
        repo.docs[c.draft.id,RECEIPT_ROLE]=('confirmation.json','text/plain',json.dumps(receipt).encode())
        with pytest.raises(ValueError,match='confirmation differs'):
            _deployment_results_session(service,session,repo.get(c.draft.id),c.account,revision)

@pytest.mark.parametrize('confirmed',[False,None,1,'yes'])
def test_confirmation_must_be_literal_true_before_connection(confirmed):
    from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
    service=object.__new__(DeploymentMonthlyInputs)
    with pytest.raises(ValueError,match='Explicit'):
        service.save_associated_gain_loss(None,'a','d',0,None,confirm_same_account=confirmed)


def test_wrong_report_month_and_masked_suffix_rejected(association):
    c=association
    text=report().replace('10/01/2026 to 10/31/2026','09/01/2026 to 09/30/2026')
    c.paths[ROLE].write_text(text)
    with pytest.raises(ValueError):make_receipt(c.session,c.draft,'actor',c.known,c.paths[ROLE],c.paths['schwab_brokerage_statement'],b'r',b's')
    # A nickname can contain visible masked evidence; it must not conflict.
    c.paths[ROLE].write_text(report().replace('My_Brokerage','My_Brokerage …999'))
    with pytest.raises(ValueError,match='suffix conflicts'):make_receipt(c.session,c.draft,'actor',c.known,c.paths[ROLE],c.paths['schwab_brokerage_statement'],b'r',b's')
    c.paths[ROLE].write_text(report().replace('My_Brokerage','My_Brokerage 8765-4321'))
    with pytest.raises(ValueError,match='full identifier'):make_receipt(c.session,c.draft,'actor',c.known,c.paths[ROLE],c.paths['schwab_brokerage_statement'],b'r',b's')


def test_missing_statement_blocks_save_without_inserting_report(saving):
    c=saving;repo=c.repositories[c.account]
    del repo.docs[c.selected.id,'schwab_brokerage_statement']
    before=dict(repo.docs)
    with pytest.raises(ValueError,match='Statement'):save_report(c)
    assert c.repositories[c.account].docs==before


def test_changed_statement_invalidates_preview(saving):
    c=saving;save_report(c);repo=c.repositories[c.account]
    repo.upload(c.selected.id,'schwab_brokerage_statement','updated.txt','text/plain',(statement()+'\n').encode(),replace=True)
    result=c.associated.preview(c.admin,c.account,c.selected.id,4,forex_applicable=False,crypto_applicable=False)
    assert not next(row for row in result.validations if row.role=='account:'+ROLE).valid


def test_malformed_csv_quotes_rejected():
    with pytest.raises(ValueError,match='quoting'):read(report()+'"unterminated')
