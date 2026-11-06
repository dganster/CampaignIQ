from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
import pytest
from campaigniq import account_no_trading as mode
from campaigniq.no_trading_statement import analyze
from campaigniq.no_trading_rehearsal_sources import sources
from campaigniq.persistence.lot_book_store import deserialize_lot_book
from tests.test_gain_loss_association import saving,bound_context,context
from tests.test_account_local_publication import MemoryStorage
from tests.test_multiasset_application import application

def revision(c):return c.repositories[c.account].get(c.selected.id).revision

def review(c):return c.associated.no_trading_preview(c.admin,c.account,c.selected.id,revision(c))
def save(c,**extra):
    opts=dict(reviewed_sha256=review(c)['review_sha256'],confirm_no_trading=True,confirm_cash_income=True);opts.update(extra)
    return c.associated.save_no_trading_confirmation(c.admin,c.account,c.selected.id,revision(c),**opts)
def publish(c):return application(c).publish(c.admin,c.account,c.selected.id,revision(c),confirm_publication=True,forex_applicable=False,crypto_applicable=False)

@pytest.fixture
def carry(saving,monkeypatch):
    c=saving;upload,rows=sources('12345678');repo=c.repositories[c.account]
    repo.upload(c.selected.id,'schwab_brokerage_statement',upload.name,upload.media_type,upload.content,replace=True)
    store=MemoryStorage(c.account);store.rows=rows;c.artifacts[c.account]=store
    c.associated.gateway._directory.registry_id=c.registry
    from campaigniq import account_forex_daily_sources as daily
    monkeypatch.setattr(daily,'current_owner',lambda service,session:c.directory.user(c.admin).id)
    return c

def test_owner_statement_review_save_publish_and_independent_reader_preserve_lots(carry):
    c=carry;before=deepcopy(c.artifacts[c.account].rows);docs=deepcopy(c.repositories[c.account].docs)
    checked=review(c);assert checked['review']['financials']['income_usd']=='59.00'
    assert not checked['owner_confirmed'] and c.artifacts[c.account].rows==before
    save(c);assert review(c)['owner_confirmed'];assert save(c)==revision(c)
    assert c.associated.preview(c.admin,c.account,c.selected.id,revision(c),forex_applicable=False,crypto_applicable=False).inputs_ready
    assert c.associated.reconcile(c.admin,c.account,c.selected.id,revision(c),forex_applicable=False,crypto_applicable=False).inventory_reconciled
    publish(c)
    from campaigniq.account_deployment_results import _deployment_results_session
    with c.associated.gateway.transaction(c.admin,c.account) as session:
        result=_deployment_results_session(c.associated,session,session.drafts.get(c.selected.id),c.account,revision(c))
    assert result.no_trading_month and Decimal(result.cash_income_usd)==59 and Decimal(result.brokerage_realized_usd)==0
    old=deserialize_lot_book(before['2026-09-lot-book.json']).lot_book
    new=deserialize_lot_book(c.artifacts[c.account].rows['2026-10-lot-book.json']).lot_book
    assert old._lots==new._lots and old._next_id==new._next_id
    from campaigniq.dashboard_sources import load_dashboard_sources
    summaries,_,_=load_dashboard_sources(c.artifacts[c.account])
    assert summaries[-1].no_trading_month and summaries[-1].statement_cash_income==59
    assert summaries[-1].combined_realized_pnl==0
    assert {k:c.artifacts[c.account].rows[k] for k in before}==before
    assert all(c.repositories[c.account].docs[k]==v for k,v in docs.items())

@pytest.mark.parametrize('change', ['trades','transfer','expense','other','details','income','realized','positions','expiry','foreign_statement','missing_predecessor','active_report'])
def test_ambiguous_or_active_statement_cannot_be_confirmed(carry,change):
    c=carry;repo=c.repositories[c.account];name,media,raw=repo.document(c.selected.id,'schwab_brokerage_statement')
    text=raw.decode()
    if change=='trades':text=text.replace('$0.00 $0.00 $0.00 $0.00 $59.00','$0.00 $0.00 $1.00 $0.00 $59.00')
    elif change=='transfer':text=text.replace('$8,963.90 $0.00','$8,963.90 $1.00')
    elif change=='expense':text=text.replace('$59.00 $0.00','$59.00 $1.00')
    elif change=='other':text=text.replace('Other Activity $0.00','Other Activity $1.00')
    elif change=='details':text=text.replace('Dividend Qual. Dividend','Sale Short Sale')
    elif change=='income':text=text.replace('SYNTHETIC ETF 59.00','SYNTHETIC ETF 60.00')
    elif change=='realized':text=text.replace('This 0.00','This 1.00')
    elif change=='positions':text=text.replace('100.0000 100.0000','101.0000 100.0000')
    elif change=='expiry':
        from dataclasses import replace
        from campaigniq.persistence.lot_book_store import serialize_lot_book
        book=deserialize_lot_book(c.artifacts[c.account].rows['2026-09-lot-book.json']).lot_book
        old=next(i for i in book._lots if hasattr(i,'expiration'));rows=book._lots.pop(old);new=replace(old,expiration=date(2026,10,16));book._lots[new]=[replace(l,instrument=new) for l in rows]
        c.artifacts[c.account].rows['2026-09-lot-book.json']=serialize_lot_book(period_end=date(2026,9,30),lot_book=book)
    elif change=='foreign_statement':text=text.replace('1234-5678','8765-4321')
    elif change=='missing_predecessor':del c.artifacts[c.account].rows['2026-09-lot-book.json']
    elif change=='active_report':repo.upload(c.selected.id,'thinkorswim_trade_history','activity.csv','text/csv',b'activity')
    repo.docs[c.selected.id,'schwab_brokerage_statement']=(name,media,text.encode())
    before=deepcopy(repo.__dict__);artifacts=deepcopy(c.artifacts[c.account].rows)
    with pytest.raises((ValueError,FileNotFoundError)):save(c)
    assert repo.__dict__==before and c.artifacts[c.account].rows==artifacts

@pytest.mark.parametrize('suffix',['lot-book.json','position-journal.json','realized-attributions.json','forex-settlement-attributions.json','lifecycle-transitions.json','boundary-completeness.json','market-applicability.json','import-provenance.json','no-trading-month.json','account-publication-receipt.json','finalized.json'])
def test_every_publication_insert_failure_rolls_back_whole_month(carry,monkeypatch,suffix):
    c=carry;save(c);store=c.artifacts[c.account];before=deepcopy(store.rows);real=store.write_text;reached=[]
    def fail(key,text):
        real(key,text)
        if key=='2026-10-'+suffix:reached.append(key);raise RuntimeError('Injected generation failure')
    monkeypatch.setattr(store,'write_text',fail)
    with pytest.raises(RuntimeError):publish(c)
    assert reached and store.rows==before

@pytest.mark.parametrize('case',['stale','foreign','missing_confirmation','forex','crypto','disabled','wrong_review','owner_checkbox'])
def test_current_owner_revision_review_choices_and_capability_required(carry,case):
    c=carry;before=deepcopy(c.artifacts[c.account].rows)
    if case in ('wrong_review','owner_checkbox'):
        with pytest.raises(ValueError):save(c,**({'reviewed_sha256':'0'*64} if case=='wrong_review' else {'confirm_no_trading':False}))
    else:
        if case!='missing_confirmation':save(c)
        app=application(c,enabled=case!='disabled')
        with pytest.raises((ValueError,PermissionError)):
            app.publish(c.member if case=='foreign' else c.admin,c.account,c.selected.id,revision(c)-1 if case=='stale' else revision(c),confirm_publication=True,
                forex_applicable=case=='forex',crypto_applicable=case=='crypto')
    assert c.artifacts[c.account].rows==before

@pytest.mark.parametrize('suffix',['no-trading-month.json','position-journal.json','lot-book.json','account-publication-receipt.json'])
def test_independent_result_rejects_changed_committed_financial_evidence_without_repair(carry,suffix):
    c=carry;save(c);publish(c);store=c.artifacts[c.account];store.rows['2026-10-'+suffix]='{}';before=deepcopy(store.rows)
    from campaigniq.account_deployment_results import _deployment_results_session
    with c.associated.gateway.transaction(c.admin,c.account) as session:
        with pytest.raises((ValueError,KeyError)):_deployment_results_session(c.associated,session,session.drafts.get(c.selected.id),c.account,revision(c))
    assert store.rows==before

def test_zero_income_month_requires_no_invented_transactions():
    upload,_=sources(income='0.00')
    value=analyze(upload.content.decode(),start=date(2026,10,1),end=date(2026,10,31))
    assert value['cash_income']==[] and Decimal(value['income_usd'])==0

def test_full_statement_header_allows_matching_masked_continuation_but_never_mask_only_or_conflict():
    from campaigniq.broker_account_binding import document_identity
    full='Account Number Statement Period\nSYNTHETIC OWNER 1234-5678 October 1-31, 2026\n\n\n'
    continued='Account Number Statement Period\nSYNTHETIC OWNER ****-*678 October 1-31, 2026\n'
    assert document_identity('schwab_brokerage_statement',full+continued).suffix=='5678'
    for raw in (continued,full+continued.replace('*678','*999'),continued+'\n\n'+full):
        with pytest.raises(ValueError):document_identity('schwab_brokerage_statement',raw)

def test_november_receives_identical_opening_but_expiring_options_need_normal_activity_workflow(carry):
    c=carry;save(c);publish(c);store=c.artifacts[c.account]
    from campaigniq.persistence.authoritative_lot_state import load_preceding_authoritative_state_from_storage
    previous=deserialize_lot_book(store.rows['2026-09-lot-book.json']).lot_book
    found=load_preceding_authoritative_state_from_storage(store,period_start=date(2026,11,1))
    assert found[0]==date(2026,10,31) and found[2]._lots==previous._lots and found[2]._next_id==previous._next_id
    october={k:v for k,v in store.rows.items() if k.startswith('2026-10-')}
    draft=c.associated.create(c.admin,c.account,2026,11);upload,_=sources('12345678',month=11,income='0.00')
    rev=c.associated.save(c.admin,c.account,draft.id,0,'schwab_brokerage_statement',upload)
    with pytest.raises(ValueError,match='expires'):
        c.associated.no_trading_preview(c.admin,c.account,draft.id,rev)
    assert {k:store.rows[k] for k in october}==october


def test_coherently_rehashed_journal_and_receipt_cannot_replace_source_replay(carry):
    from hashlib import sha256
    c=carry;save(c);publish(c);store=c.artifacts[c.account];prefix='2026-10-'
    journal=json.loads(store.rows[prefix+'position-journal.json']);journal['entries'][0]['lots'][0]['lot_id']='forged'
    text=json.dumps(journal,sort_keys=True,indent=2)+'\n';store.rows[prefix+'position-journal.json']=text
    body=json.loads(store.rows[prefix+'no-trading-month.json']);body['canonical_artifact_sha256'][prefix+'position-journal.json']=sha256(text.encode()).hexdigest()
    text=json.dumps(body,sort_keys=True,indent=2)+'\n';store.rows[prefix+'no-trading-month.json']=text
    receipt=json.loads(store.rows[prefix+'account-publication-receipt.json']);receipt['no_trading_generation']['sha256']=sha256(text.encode()).hexdigest()
    store.rows[prefix+'account-publication-receipt.json']=json.dumps(receipt)
    from campaigniq.persistence.no_trading_month import load_income
    assert load_income(store,period_end=date(2026,10,31))==59
    before=deepcopy(store.rows)
    from campaigniq.account_deployment_results import _deployment_results_session
    with c.associated.gateway.transaction(c.admin,c.account) as session:
        with pytest.raises(ValueError):_deployment_results_session(c.associated,session,session.drafts.get(c.selected.id),c.account,revision(c))
    assert store.rows==before

@pytest.mark.parametrize('published',[False,True])
def test_edit_grantee_is_rejected_as_nonowner_before_publication_guard(carry,published):
    from campaigniq.account_administration import Permission
    c=carry;checked=review(c)
    if published:save(c);publish(c)
    member=c.directory.user(c.member).id
    c.directory=c.directory.set_grant(c.admin,member,c.account,Permission.EDIT,expected_revision=c.directory.revision)
    before=deepcopy(c.artifacts[c.account].rows);docs=deepcopy(c.repositories[c.account].__dict__)
    with pytest.raises(PermissionError,match='Only the account owner'):
        c.associated.save_no_trading_confirmation(c.member,c.account,c.selected.id,revision(c),
            reviewed_sha256=checked['review_sha256'],confirm_no_trading=True,confirm_cash_income=True)
    if published:
        with pytest.raises(ValueError,match='Existing monthly financial artifacts'):
            c.associated.save_no_trading_confirmation(c.admin,c.account,c.selected.id,revision(c),
                reviewed_sha256=checked['review_sha256'],confirm_no_trading=True,confirm_cash_income=True)
    assert c.artifacts[c.account].rows==before and c.repositories[c.account].__dict__==docs
