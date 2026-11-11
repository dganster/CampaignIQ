from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
import pytest
from campaigniq.account_crypto_publication import _publish_crypto_in_session,verify_published
from campaigniq.account_deployment_results import _deployment_results_session
from campaigniq.crypto_account_rehearsal_sources import sources
from tests.test_account_crypto_statement_sources import crypto,save,rev
from tests.test_gain_loss_association import saving,bound_context,context


@pytest.fixture
def ready(crypto):
    c=crypto;repo=c.repositories[c.account]
    uploads,_,_=sources('12345678')
    for role in ('schwab_brokerage_statement','schwab_opening_position_snapshot','schwab_realized_gain_loss'):
        u=uploads[role];repo.upload(c.selected.id,role,u.name,u.media_type,u.content,replace=(c.selected.id,role) in repo.docs)
    name,media,raw=repo.document(c.selected.id,'thinkorswim_trade_history')
    marker=b',Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type\n'
    assert marker in raw
    raw=raw.replace(marker,marker+b',10/15/26 10:00:00,STOCK,BUY,+10,TO OPEN,SPY,,,STOCK,100,100,LMT\n')
    repo.upload(c.selected.id,'thinkorswim_trade_history',name,media,raw,replace=True)
    save(c)
    from tests.test_account_local_publication import MemoryStorage
    c.artifacts[c.account]=MemoryStorage(c.account)
    c.associated.gateway._directory.registry_id=c.registry
    return c


def publish(c):
    storage=c.artifacts[c.account];before=deepcopy(storage.rows)
    try:
        with c.associated.gateway.transaction(c.admin,c.account,write=True) as session:
            session._check=lambda **k:None
            return _publish_crypto_in_session(c.associated,session,c.directory.user(c.admin).id,c.account,c.selected.id,rev(c),
                forex_applicable=False,crypto_applicable=True)
    except BaseException:storage.rows=before;raise


def test_complete_crypto_month_uses_original_sources_and_documented_costs(ready):
    c=ready;before=deepcopy(c.repositories[c.account].docs);out=publish(c)
    assert out.local_published and not out.production_publication_authorized
    report=json.loads(c.artifacts[c.account].read_text('2026-10-crypto.json'))
    assert Decimal(report['ending_quantities']['BTC/USD'])==Decimal('.005929')
    assert sum(Decimal(l['cost_usd']) for l in report['ending_lots']['BTC/USD'])==500
    assert report['original_export_observations']['cash_control_reconciled']==report['cash_control_reconciled']
    with c.associated.gateway.transaction(c.admin,c.account) as session:
        draft=session.drafts.get(c.selected.id)
        assert verify_published(c.associated,session,draft)
        result=_deployment_results_session(c.associated,session,draft,c.account,rev(c))
        assert sum(Decimal(q) for _,q in result.positions)==110
        assert result.crypto_applicable and 'owner-approved' in result.crypto_status
    assert c.repositories[c.account].docs==before
    with pytest.raises(ValueError):publish(c)


@pytest.mark.parametrize('suffix',['crypto.json','account-publication-receipt.json','finalized.json'])
def test_partial_generation_rolls_back(ready,monkeypatch,suffix):
    c=ready;storage=c.artifacts[c.account];before=deepcopy(storage.rows);real=storage.write_text
    def fail(key,text):
        real(key,text)
        if key.endswith(suffix):raise RuntimeError('injected')
    monkeypatch.setattr(storage,'write_text',fail)
    with pytest.raises(RuntimeError):publish(c)
    assert storage.rows==before


@pytest.mark.parametrize('case',['record','receipt','missing_descriptor','missing_embedded','missing_both'])
def test_conflict_withholds_whole_result_without_repair(ready,case):
    c=ready;publish(c);storage=c.artifacts[c.account]
    key='2026-10-'+('account-publication-receipt.json' if case in ('receipt','missing_descriptor') else 'crypto.json')
    body=json.loads(storage.read_text(key))
    if case=='record':body['ending_quantities']['BTC/USD']='999'
    elif case=='receipt':body['approved_crypto_generation']['candidate_sha256']='0'*64
    elif case=='missing_descriptor':del body['approved_crypto_generation']
    else:
        del body['approved_statement_financials']
        if case=='missing_both':
            rkey='2026-10-account-publication-receipt.json';r=json.loads(storage.read_text(rkey))
            del r['approved_crypto_generation'];storage.write_text(rkey,json.dumps(r))
    storage.write_text(key,json.dumps(body));before=deepcopy(storage.rows)
    with c.associated.gateway.transaction(c.admin,c.account) as session:
        with pytest.raises(ValueError):_deployment_results_session(c.associated,session,session.drafts.get(c.selected.id),c.account,rev(c))
    assert storage.rows==before


def test_combined_futures_crypto_and_securities_share_one_generation(ready):
    import csv,io
    from campaigniq.futures_rehearsal_sources import FILLS,LEDGER,CASH_HEADER,statement_pdf
    from campaigniq.import_draft_bridge import RestoredUpload
    from campaigniq.account_futures_publication import _publish_futures_in_session
    c=ready;repo=c.repositories[c.account]
    name,media,raw=repo.document(c.selected.id,'thinkorswim_trade_history')
    def rows(items):
        out=io.StringIO();csv.writer(out,lineterminator='\n').writerows(items);return out.getvalue().encode()
    marker=b',10/15/26 10:00:00,STOCK'
    raw=raw.replace(marker,rows(FILLS)+marker)
    raw=raw.replace(b'Forex Statements\n',b'Futures Statements\n'+rows([CASH_HEADER,*LEDGER])+b'\nForex Statements\n')
    repo.upload(c.selected.id,'thinkorswim_trade_history',name,media,raw,replace=True)
    save(c,replace=True)
    c.associated.confirm_empty_futures_opening(c.admin,c.account,c.selected.id,rev(c),confirm_empty_opening=True)
    c.associated.save_futures_statement(c.admin,c.account,c.selected.id,rev(c),
        RestoredUpload('future.pdf','application/pdf',statement_pdf()),confirm_same_account=True)
    c.associated.confirm_empty_futures_closing(c.admin,c.account,c.selected.id,rev(c),confirm_empty_closing=True)
    with c.associated.gateway.transaction(c.admin,c.account,write=True) as session:
        session._check=lambda **k:None
        _publish_futures_in_session(c.associated,session,c.directory.user(c.admin).id,c.account,c.selected.id,rev(c),
            forex_applicable=False,crypto_applicable=True)
        result=_deployment_results_session(c.associated,session,session.drafts.get(c.selected.id),c.account,rev(c))
        assert result.futures_applicable and Decimal(result.futures_realized_usd)==-816
        assert result.crypto_applicable and sum(Decimal(q) for _,q in result.positions)==110
        assert verify_published(c.associated,session,session.drafts.get(c.selected.id))


def test_unified_review_preserves_approved_crypto_without_futures_or_writes(ready):
    c=ready;before=deepcopy(c.repositories[c.account].__dict__);financial=deepcopy(c.artifacts[c.account].rows)
    result=c.associated.monthly_source_preview(c.admin,c.account,c.selected.id,rev(c),forex_applicable=False,crypto_applicable=True,futures_applicable=False)
    assert result['all_source_controls_ready'] and result['futures'] is None
    assert result['crypto']['approved_source_candidate_sha256']
    assert c.repositories[c.account].__dict__==before and c.artifacts[c.account].rows==financial
