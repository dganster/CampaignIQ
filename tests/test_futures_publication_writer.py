from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import json

import pytest
from campaigniq.account_futures_publication import _publish_futures_in_session
from campaigniq.account_deployment_results import load_deployment_results
from campaigniq.dashboard_sources import load_dashboard_sources
from campaigniq.futures_rehearsal_sources import sources,statement_pdf
from campaigniq.import_draft_bridge import RestoredUpload
from tests.test_gain_loss_association import saving,bound_context,context


@pytest.fixture
def ready(saving,monkeypatch):
    c=saving;service=c.associated;repo=c.repositories[c.account]
    from tests.test_account_local_publication import MemoryStorage
    c.artifacts[c.account]=MemoryStorage(c.account)
    service.gateway._directory.registry_id=c.registry
    real=service.gateway.transaction
    @contextmanager
    def transaction(*args,**kwargs):
        with real(*args,**kwargs) as session:
            session._check=lambda **k:None
            yield session
    monkeypatch.setattr(service.gateway,'transaction',transaction)
    for role,upload in sources().items():
        repo.upload(c.selected.id,role,upload.name,upload.media_type,upload.content,replace=role=='schwab_brokerage_statement')
    def rev():return repo.get(c.selected.id).revision
    service.confirm_empty_futures_opening(c.admin,c.account,c.selected.id,rev(),confirm_empty_opening=True)
    service.save_futures_statement(c.admin,c.account,c.selected.id,rev(),
        RestoredUpload('synthetic.pdf','application/pdf',statement_pdf()),confirm_same_account=True)
    service.confirm_empty_futures_closing(c.admin,c.account,c.selected.id,rev(),confirm_empty_closing=True)
    c.revision=rev();return c


def publish(c,**flags):
    storage=c.artifacts[c.account];before=dict(storage.rows)
    try:
        with c.associated.gateway.transaction(c.admin,c.account,write=True) as session:
            return _publish_futures_in_session(c.associated,session,c.directory.user(c.admin).id,c.account,c.selected.id,c.revision,
                forex_applicable=flags.get('forex',False),crypto_applicable=flags.get('crypto',False))
    except BaseException:storage.rows=before;raise


def test_whole_generation_writer_reports_futures_once_and_preserves_original_provenance(ready):
    c=ready;docs=deepcopy(c.repositories[c.account].__dict__);storage=c.artifacts[c.account]
    result=publish(c)
    assert '2026-10-futures.json' in result.artifact_keys
    out=load_deployment_results(c.associated,c.admin,c.account,c.selected.id,c.revision)
    assert out.futures_realized_usd=='-816.00' and sum(Decimal(q) for _,q in out.positions)==110
    summaries,_,_=load_dashboard_sources(storage)
    assert summaries[0].combined_realized_pnl==-816
    provenance=json.loads(storage.read_text('2026-10-import-provenance.json'))
    from hashlib import sha256
    history=c.repositories[c.account].document(c.selected.id,'thinkorswim_trade_history')[2]
    assert next(r for r in provenance['inputs'] if r['role']=='thinkorswim_trade_history')['sha256']==sha256(history).hexdigest()
    assert c.repositories[c.account].__dict__==docs
    before=dict(storage.rows)
    with pytest.raises(ValueError):publish(c)
    assert storage.rows==before


@pytest.mark.parametrize('suffix',['futures.json','account-publication-receipt.json','lot-book.json','finalized.json'])
def test_failure_after_any_financial_component_rolls_back_every_row(ready,monkeypatch,suffix):
    c=ready;storage=c.artifacts[c.account];real=storage.write_text;seen=[]
    def fail(key,text):
        seen.append(key);real(key,text)
        if key.endswith(suffix):raise RuntimeError('injected after insert')
    monkeypatch.setattr(storage,'write_text',fail)
    with pytest.raises(RuntimeError):publish(c)
    assert not storage.rows
    if suffix=='finalized.json':assert seen[-1]=='2026-10-finalized.json'


def test_missing_crypto_evidence_blocks_before_first_write(ready,monkeypatch):
    import campaigniq.importers.thinkorswim.crypto_reader as module
    monkeypatch.setattr(module,'read_crypto_report',lambda *a,**k:dict(fills=[{}],holdings_snapshot={},cash_ledger=[],fill_control_reconciled=True,cash_control_reconciled=False))
    for flag in (True,False):
        with pytest.raises(ValueError,match='source controls'):publish(ready,crypto=flag)
    assert not ready.artifacts[ready.account].rows


def test_source_conflict_blocks_all_writes_and_marker_is_last(ready,monkeypatch):
    from campaigniq import account_futures_publication as module
    c=ready;real=module.build_candidate;calls=[]
    def changed(*args):
        from dataclasses import replace
        r=real(*args);calls.append(1)
        return replace(r,record_json=r.record_json+' ') if len(calls)>1 else r
    monkeypatch.setattr(module,'build_candidate',changed)
    with pytest.raises(ValueError,match='changed during generation'):publish(c)
    assert not c.artifacts[c.account].rows
