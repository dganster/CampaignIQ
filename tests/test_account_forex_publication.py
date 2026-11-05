from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
import pytest

from campaigniq import account_forex_daily_sources as daily
from campaigniq import account_forex_financial_policy as policy
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.forex_daily_rehearsal_sources import upload
from campaigniq.persistence.lot_book_store import deserialize_lot_book
from tests.test_account_forex_cash_receipts import sources,saving,bound_context,context,save as save_cash
from tests.test_forex_daily_source_receipts import save as save_daily,revision,repo
from tests.test_forex_financial_policy import approve
from tests.test_account_forex_inventory_preview import Storage
from tests.test_account_local_publication import MemoryStorage
from tests.test_account_deployment_publication import FIXTURES
from tests.test_multiasset_application import application


@pytest.fixture
def combined(sources,monkeypatch):
    from campaigniq.forex_publication_rehearsal_sources import sources as publication_sources
    c=sources;store=repo(c)
    uploads,c.cash,c.supp,c.daily,previous,current,future=publication_sources('12345678','3607739',combined=True)
    for role,u in uploads.items():
        store.upload(c.selected.id,role,u.name,u.media_type,u.content,replace=(c.selected.id,role) in store.docs)
    storage=MemoryStorage(c.account);storage.rows=Storage().rows;c.artifacts[c.account]=storage
    c.associated.gateway._directory.registry_id=c.registry
    monkeypatch.setattr(daily,'current_roles',lambda session,draft:{r for (did,r) in session.drafts.docs if did==draft.id and r.startswith(daily.PREFIX)})
    monkeypatch.setattr(daily,'current_owner',lambda service,session:c.directory.user(c.admin).id)
    c.associated.confirm_empty_futures_opening(c.admin,c.account,c.selected.id,revision(c),confirm_empty_opening=True)
    c.associated.save_futures_statement(c.admin,c.account,c.selected.id,revision(c),future,confirm_same_account=True)
    c.associated.confirm_empty_futures_closing(c.admin,c.account,c.selected.id,revision(c),confirm_empty_closing=True)
    checked=c.associated.review_crypto_statements(c.admin,c.account,c.selected.id,revision(c),previous,current)
    c.associated.save_crypto_statement_sources(c.admin,c.account,c.selected.id,revision(c),previous,current,
        reviewed_preview_sha256=checked['reviewed_preview_sha256'],confirm_same_crypto_account=True,confirm_source_choices=True)
    save_cash(c);save_daily(c);approve(c)
    return c


@pytest.fixture
def ready(sources,monkeypatch):
    c=sources;store=repo(c)
    name,media,raw=store.document(c.selected.id,'thinkorswim_trade_history')
    marker=b',Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type\n'
    raw=raw.replace(marker,marker+b',10/15/26 10:00:00,STOCK,BUY,+10,TO OPEN,SPY,,,STOCK,100,100,LMT\n')
    raw+=b'\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n\n'
    store.upload(c.selected.id,'thinkorswim_trade_history',name,media,raw,replace=True)
    for role,filename,old,new in (
        ('schwab_brokerage_statement','account-month-closing.txt','January 1-31, 2025','October 1-31, 2026'),
        ('schwab_realized_gain_loss','account-month-realized.txt','01/01/2025 to 01/31/2025','10/01/2026 to 10/31/2026')):
        text=(FIXTURES/filename).read_text().replace(old,new)
        if role=='schwab_brokerage_statement':text=text.replace('110.0000','10.0000').replace('11,000.00','1,000.00')
        store.upload(c.selected.id,role,filename,'text/plain',text.encode(),replace=role=='schwab_brokerage_statement')
    storage=MemoryStorage(c.account);storage.rows=Storage().rows;c.artifacts[c.account]=storage
    c.associated.gateway._directory.registry_id=c.registry
    save_cash(c);c.daily=upload('3607739')
    monkeypatch.setattr(daily,'current_roles',lambda session,draft:{r for (did,r) in session.drafts.docs if did==draft.id and r.startswith(daily.PREFIX)})
    monkeypatch.setattr(daily,'current_owner',lambda service,session:c.directory.user(c.admin).id)
    save_daily(c);approve(c)
    return c


def publish(c,**kwargs):
    return application(c).publish(c.admin,c.account,c.selected.id,revision(c),confirm_publication=True,
        forex_applicable=True,crypto_applicable=False,**kwargs)


def test_approved_forex_publishes_complete_source_bound_generation_and_preserves_carry_identity(ready):
    c=ready;before=deepcopy(repo(c).docs);september={k:v for k,v in c.artifacts[c.account].rows.items() if k.startswith('2026-09')}
    publish(c)
    from campaigniq.account_deployment_results import _deployment_results_session
    with c.associated.gateway.transaction(c.admin,c.account) as session:
        loaded=_deployment_results_session(c.associated,session,session.drafts.get(c.selected.id),c.account,revision(c))
    assert loaded.forex_policy_approved and Decimal(loaded.forex_settled_usd)==-10
    assert Decimal(loaded.forex_financing_usd)==-1 and Decimal(loaded.forex_ending_cash_usd)==989
    assert loaded.forex_settlements[0][3]=='synthetic-existing-forex-campaign'
    assert loaded.positions==( ('SPY','10'), )
    rows=c.artifacts[c.account].rows
    record=json.loads(rows['2026-10-forex-approved-generation.json'])
    assert record['financials']['net_cash_change_usd']=='-11.00'
    audit=json.loads(rows['2026-10-forex-source-resolution.json'])
    assert audit['actual_close_allocations']==[dict(reference='1001',lot_id='synthetic-forex-opening',campaign_id='synthetic-existing-forex-campaign',quantity='50000')]
    assert audit['original_candidate']['calculation']['raw_csv_controls']['mtd_settled_pl_usd']=='-9.00'
    assert json.loads(rows['2026-10-forex-settlement-attributions.json'])['attributions'][0]['settlement']['settlement_at'] is None
    assert repo(c).docs==before and {k:rows[k] for k in september}==september
    assert all(not isinstance(i, __import__('campaigniq.domain.value_objects.forex_pair',fromlist=['ForexPair']).ForexPair)
        for i in deserialize_lot_book(rows['2026-10-lot-book.json']).lot_book.instruments())
    from campaigniq.dashboard_sources import load_dashboard_sources
    summaries,_,_=load_dashboard_sources(c.artifacts[c.account])
    assert summaries[0].forex_policy_approved and summaries[0].forex_financing_pnl==-1
    assert summaries[0].combined_with_forex_financing==-11


@pytest.mark.parametrize('case',['disabled','publication_disabled','omit_forex','foreign','stale','confirmation'])
def test_public_forex_route_rechecks_capability_scope_revision_and_confirmation(ready,case):
    c=ready;before=deepcopy(c.artifacts[c.account].rows)
    app=application(c,enabled=case!='disabled',publication=case!='publication_disabled')
    with pytest.raises((ValueError,PermissionError)):
        app.publish(c.member if case=='foreign' else c.admin,c.account,c.selected.id,
            revision(c)-1 if case=='stale' else revision(c),confirm_publication=case!='confirmation',
            forex_applicable=case!='omit_forex',crypto_applicable=False)
    assert c.artifacts[c.account].rows==before


@pytest.mark.parametrize('suffix',['realized-attributions.json','forex-settlement-attributions.json','forex-source-resolution.json',
    'forex-approved-generation.json','lot-book.json','position-journal.json','account-publication-receipt.json','finalized.json'])
def test_failure_after_every_new_financial_component_rolls_back_generation(ready,monkeypatch,suffix):
    c=ready;storage=c.artifacts[c.account];before=deepcopy(storage.rows);docs=deepcopy(repo(c).docs);real=storage.write_text
    def fail(key,text):
        real(key,text)
        if key.startswith('2026-10-') and key.endswith(suffix):raise RuntimeError('Injected generation failure')
    monkeypatch.setattr(storage,'write_text',fail)
    with pytest.raises(RuntimeError):publish(c)
    assert storage.rows==before and repo(c).docs==docs


@pytest.mark.parametrize('case',['record','descriptor','missing_record','missing_descriptor','missing_both','coherent_money','coherent_journal'])
def test_independent_source_reader_withholds_corrupt_or_self_consistent_forged_generation(ready,case):
    c=ready;publish(c);storage=c.artifacts[c.account];prefix='2026-10-'
    key=prefix+'forex-approved-generation.json';rkey=prefix+'account-publication-receipt.json'
    body=json.loads(storage.rows[key]);receipt=json.loads(storage.rows[rkey])
    if case=='record':body['financials']['trading_usd']='999'
    elif case=='descriptor':receipt['approved_forex_generation']['sha256']='0'*64
    elif case in ('missing_record','missing_both'):del storage.rows[key]
    if case in ('missing_descriptor','missing_both'):del receipt['approved_forex_generation']
    if case in ('coherent_money','coherent_journal'):
        from hashlib import sha256
        from campaigniq.persistence.approved_forex_month import reference
        if case=='coherent_journal':
            target=prefix+'position-journal.json';journal=json.loads(storage.rows[target])
            journal['entries'][0]['lots'][0]['lot_id']='forged-lot'
            storage.rows[target]=json.dumps(journal,sort_keys=True,indent=2)+'\n'
        else:
            target=prefix+'forex-settlement-attributions.json';attrs=json.loads(storage.rows[target])
            attrs['attributions'][0]['settlement']['settlement_pl_usd']='-9.00'
            storage.rows[target]=json.dumps(attrs,sort_keys=True,indent=2)+'\n'
            body['financials'].update(trading_usd='-9.00',ending_cash_usd='990.00',net_cash_change_usd='-10.00')
            body['approved_source_snapshot']['candidate']['artifact']['calculation']['totals']=deepcopy(body['financials'])
        body['canonical_artifact_sha256'][target]=sha256(storage.rows[target].encode()).hexdigest()
        text=json.dumps(body,sort_keys=True,indent=2)+'\n';storage.rows[key]=text
        receipt['approved_forex_generation']=reference(body,text)
    elif case=='record':storage.rows[key]=json.dumps(body)
    storage.rows[rkey]=json.dumps(receipt)
    if case in ('coherent_money','coherent_journal'):
        from campaigniq.persistence.approved_forex_month import load_published
        assert load_published(storage,period_start=date(2026,10,1),period_end=date(2026,10,31)) is not None
    before=deepcopy(storage.rows)
    from campaigniq.account_deployment_results import _deployment_results_session
    with c.associated.gateway.transaction(c.admin,c.account) as session:
        with pytest.raises(ValueError):_deployment_results_session(c.associated,session,session.drafts.get(c.selected.id),c.account,revision(c))
    assert storage.rows==before


def test_changed_source_approval_is_not_accepted_as_publication_repair(ready):
    c=ready;store=repo(c);name,media,raw=store.document(c.selected.id,policy.ROLE)
    body=json.loads(raw);body['reviewed_candidate_sha256']='0'*64
    store.docs[c.selected.id,policy.ROLE]=(name,media,json.dumps(body).encode())
    before=deepcopy(c.artifacts[c.account].rows)
    with pytest.raises(ValueError):publish(c)
    assert c.artifacts[c.account].rows==before


def test_combined_securities_forex_futures_crypto_generation_uses_each_approved_source(combined):
    c=combined
    future=c.associated.futures_preview(c.admin,c.account,c.selected.id,revision(c))['realized_net_usd']
    checked=c.associated.futures_monthly_preview(c.admin,c.account,c.selected.id,revision(c),forex_applicable=True,crypto_applicable=True)
    assert checked['all_source_controls_ready'] and checked['approved_forex_policy']
    application(c).publish(c.admin,c.account,c.selected.id,revision(c),confirm_publication=True,forex_applicable=True,crypto_applicable=True)
    from campaigniq.account_deployment_results import _deployment_results_session
    with c.associated.gateway.transaction(c.admin,c.account) as session:
        loaded=_deployment_results_session(c.associated,session,session.drafts.get(c.selected.id),c.account,revision(c))
    assert loaded.forex_policy_approved and Decimal(loaded.forex_settled_usd)==-10 and Decimal(loaded.forex_financing_usd)==-1
    assert loaded.futures_applicable and Decimal(loaded.futures_realized_usd)==Decimal(future)
    assert loaded.crypto_applicable and 'owner-approved' in loaded.crypto_status
    receipt=json.loads(c.artifacts[c.account].read_text('2026-10-account-publication-receipt.json'))
    assert all(name in receipt for name in ('approved_forex_generation','futures_generation','approved_crypto_generation'))


def test_next_month_uses_published_opening_without_changing_approved_forex_generation(ready):
    c=ready;publish(c);storage=c.artifacts[c.account]
    october={k:v for k,v in storage.rows.items() if k.startswith('2026-10-')}
    draft=c.associated.create(c.admin,c.account,2026,11);rev=0
    history=('Account Statement for 12345678 since 10/31/26 through 11/30/26\n\nAccount Trade History\n'
        ',Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type\n,11/15/26 10:00:00,STOCK,BUY,+1,TO OPEN,SPY,,,STOCK,100,100,LMT\n\n'
        'Cash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n\n'
        'Forex Statements\n,Date,Time,Type,Ref #,Description,Commissions & Fees,Amount,Amount(USD),Balance\n\n')
    for role,name,text in (
        ('thinkorswim_trade_history','next.csv',history),
        ('schwab_brokerage_statement','closing.txt',(FIXTURES/'account-month-closing.txt').read_text().replace('January 1-31, 2025','November 1-30, 2026').replace('110.0000','11.0000').replace('11,000.00','1,100.00')),
        ('schwab_realized_gain_loss','realized.txt',(FIXTURES/'account-month-realized.txt').read_text().replace('01/01/2025 to 01/31/2025','11/01/2026 to 11/30/2026'))):
        rev=c.associated.save(c.admin,c.account,draft.id,rev,role,RestoredUpload(name,'text/plain',text.encode()))
    application(c).publish(c.admin,c.account,draft.id,rev,confirm_publication=True,forex_applicable=False,crypto_applicable=False)
    assert {k:storage.rows[k] for k in october}==october
    assert sum(l.quantity for rows in deserialize_lot_book(storage.rows['2026-11-lot-book.json']).lot_book._lots.values() for l in rows)==11
