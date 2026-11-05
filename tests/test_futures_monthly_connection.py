from copy import deepcopy
import json
from pathlib import Path

import pytest

from campaigniq import account_futures_closing as closing
from campaigniq import account_futures_preview as statement
from campaigniq import futures_monthly_preview as monthly
from campaigniq.account_administration import Permission
from campaigniq.import_draft_bridge import RestoredUpload
from tests.test_account_futures_preview import connected,opening,saving,context,bound_context,save,PDF
from tests.test_account_futures_opening import confirm
from tests.test_account_deployment_publication import deployed,local,month,FIXTURES


def end_confirm(c,**kwargs):
    repo=c.repositories[c.account]
    return c.associated.confirm_empty_futures_closing(c.admin,c.account,c.selected.id,repo.get(c.selected.id).revision,
        confirm_empty_closing=True,**kwargs)


@pytest.fixture
def flat(connected):
    c=connected;save(c);return c


def test_empty_ending_assertion_is_scoped_and_does_not_publish(flat):
    c=flat;repo=c.repositories[c.account];before=deepcopy(repo.docs)
    assert end_confirm(c)==6
    result=c.associated.futures_preview(c.admin,c.account,c.selected.id,6)
    assert result['closing_positions_confirmed_by_owner'] and result['closing_evidence_type']=='user_confirmation'
    assert result['ending_positions_independently_verified'] is False and result['publication_authorized'] is False
    assert all(repo.docs[k]==v for k,v in before.items())
    record=json.loads(repo.document(c.selected.id,closing.ROLE)[2])
    assert record['closing_as_of']=='2026-10-31' and record['confirmed_by']==c.directory.user(c.admin).id
    assert record['broker_verified'] is False and record['account_id']==c.account
    prior=deepcopy(repo.__dict__);assert end_confirm(c)==6 and repo.__dict__==prior


@pytest.mark.parametrize('value',[False,None,1,'yes'])
def test_literal_closing_assertion_is_required_before_connection(value):
    from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
    obj=object.__new__(DeploymentMonthlyInputs)
    with pytest.raises(ValueError):obj.confirm_empty_futures_closing(None,'a','d',0,confirm_empty_closing=value)


@pytest.mark.parametrize('case',['foreign','stale','published','post_write'])
def test_rejected_closing_assertions_roll_back(flat,monkeypatch,case):
    c=flat;repo=c.repositories[c.account];before=deepcopy(repo.__dict__)
    user=c.admin;revision=5
    if case=='foreign':user=c.member
    elif case=='stale':revision=4
    elif case=='published':monkeypatch.setattr(c.artifacts[c.account],'list_keys',lambda **k:('published',))
    else:
        actual=repo.upload
        def upload(*args,**kwargs):
            result=actual(*args,**kwargs);repo.docs[c.selected.id,closing.ROLE]=('bad','text/plain',b'{}');return result
        monkeypatch.setattr(repo,'upload',upload)
    with pytest.raises((ValueError,PermissionError)):
        c.associated.confirm_empty_futures_closing(user,c.account,c.selected.id,revision,confirm_empty_closing=True)
    current=c.repositories[c.account]
    assert current.rows==before['rows'] and current.docs==before['docs'] and current.originals==before['originals']


@pytest.mark.parametrize('field,value',[('account_id','foreign'),('draft_id','foreign'),('empty_futures',1),
    ('broker_verified',True),('closing_as_of','2026-09-30'),('confirmed_source_revision',True),
    ('evidence_graph_sha256','bad'),('confirmed_at','2026-11-02')])
def test_corrupt_closing_evidence_cannot_be_read_or_overwritten(flat,field,value):
    c=flat;end_confirm(c);repo=c.repositories[c.account]
    record=json.loads(repo.document(c.selected.id,closing.ROLE)[2]);record[field]=value
    repo.docs[c.selected.id,closing.ROLE]=('bad','text/plain',json.dumps(record).encode())
    before=deepcopy(repo.docs)
    with pytest.raises(ValueError):c.associated.futures_preview(c.admin,c.account,c.selected.id,6)
    with pytest.raises(ValueError):end_confirm(c)
    assert c.repositories[c.account].docs==before


def test_changed_pdf_invalidates_ending_but_explicit_reconfirmation_is_possible(flat):
    c=flat;end_confirm(c);repo=c.repositories[c.account]
    old=repo.document(c.selected.id,closing.ROLE)
    save(c,PDF+b'new',replace=True)
    revision=c.repositories[c.account].get(c.selected.id).revision
    with pytest.raises(ValueError,match='reconfirm'):c.associated.futures_preview(c.admin,c.account,c.selected.id,revision)
    end_confirm(c);repo=c.repositories[c.account]
    assert old in repo.originals
    assert c.associated.futures_preview(c.admin,c.account,c.selected.id,repo.get(c.selected.id).revision)['closing_positions_confirmed_by_owner']


def test_nonflat_computed_book_cannot_receive_empty_confirmation(flat,monkeypatch):
    from campaigniq import account_futures_preview as module
    actual=module.connected_preview
    def nonflat(*args,**kw):
        result=actual(*args,**kw);result['calculated_ending_futures']={'/ESZ26':'1'};return result
    monkeypatch.setattr(module,'connected_preview',nonflat)
    before=deepcopy(flat.repositories[flat.account].docs)
    with pytest.raises(ValueError,match='not empty'):end_confirm(flat)
    assert flat.repositories[flat.account].docs==before


def test_edit_grantee_can_read_but_cannot_confirm_end(flat):
    c=flat;member=c.directory.user(c.member).id
    c.directory=c.directory.set_grant(c.admin,member,c.account,Permission.EDIT,expected_revision=c.directory.revision)
    with pytest.raises(PermissionError,match='owner'):
        c.associated.confirm_empty_futures_closing(c.member,c.account,c.selected.id,5,confirm_empty_closing=True)


def test_projection_preserves_original_and_all_nonfuture_rows(flat,tmp_path):
    c=flat;repo=c.repositories[c.account]
    result=c.associated.futures_preview(c.admin,c.account,c.selected.id,5)
    raw=repo.document(c.selected.id,'thinkorswim_trade_history')[2]
    path=tmp_path/'derived.csv';lineage=monthly.project_history(raw,result,path)
    assert raw==repo.document(c.selected.id,'thinkorswim_trade_history')[2]
    assert lineage['removed_futures_row_ids']==['history-row-0','history-row-1']
    assert lineage['original_sha256']!=lineage['derived_sha256'] and lineage['original_preserved']
    assert b'Opening futures position due to futures option expiration' in path.read_bytes()
    from types import SimpleNamespace
    def projected_document(key,role):
        if role!='thinkorswim_trade_history':raise FileNotFoundError(role)
        return 'derived.csv','text/csv',path.read_bytes()
    projected_session=SimpleNamespace(drafts=SimpleNamespace(document=projected_document))
    assert monthly.has_futures(projected_session,c.selected) # ledger evidence cannot be hidden by removing fill rows
    with pytest.raises(ValueError):monthly.project_history(raw,{**result,'option_execution_legs':1},path)
    with pytest.raises(ValueError):monthly.project_history(raw+b'changed',result,path)


@pytest.mark.parametrize('forex_disagreement',[False,True])
def test_monthly_source_projection_drives_real_securities_pipeline_without_writes(flat,monkeypatch,forex_disagreement):
    c=flat;repo=c.repositories[c.account]
    raw=repo.document(c.selected.id,'thinkorswim_trade_history')[2].decode()
    raw=raw.replace('\nFutures Statements\n',',10/15/26 10:00:00,STOCK,BUY,+10,TO OPEN,SPY,,,STOCK,100,100,LMT\n\nFutures Statements\n')
    raw+='\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n\nForex Statements\n,Date,Time,Type,Ref #,Description,Commissions & Fees,Amount,Amount(USD),Balance\n\n'
    repo.upload(c.selected.id,'thinkorswim_trade_history','full.csv','text/csv',raw.encode(),replace=True)
    for role,name,old,new in (
        ('schwab_brokerage_statement','account-month-closing.txt','January 1-31, 2025','October 1-31, 2026'),
        ('schwab_opening_position_snapshot','account-month-opening.txt','December 1-31, 2024','September 1-30, 2026'),
        ('schwab_realized_gain_loss','account-month-realized.txt','01/01/2025 to 01/31/2025','10/01/2026 to 10/31/2026')):
        content=(FIXTURES/name).read_text().replace(old,new).encode()
        repo.upload(c.selected.id,role,name,'text/plain',content,replace=role=='schwab_brokerage_statement')
    confirm(c);save(c);end_confirm(c)
    repo=c.repositories[c.account];before=deepcopy(repo.__dict__)
    revision=repo.get(c.selected.id).revision
    if forex_disagreement:
        from dataclasses import replace
        from decimal import Decimal
        from campaigniq.domain.value_objects.forex_pair import ForexPair
        from campaigniq.closing_inventory_reconciliation import ClosingInventoryMismatch,ClosingInventoryReconciliation
        import campaigniq.monthly_import_execution as execution
        execute=execution.execute_monthly_import
        def disagree(*args,**kwargs):
            outcome=execute(*args,**kwargs)
            return replace(outcome,closing_reconciliation=ClosingInventoryReconciliation((
                ClosingInventoryMismatch(ForexPair.from_symbol('USD/JPY'),Decimal('0'),Decimal('-50000')),)))
        monkeypatch.setattr(execution,'execute_monthly_import',disagree)
    result=c.associated.futures_monthly_preview(c.admin,c.account,c.selected.id,revision,forex_applicable=False,crypto_applicable=False)
    assert result['securities_inventory_reconciled'] is (not forex_disagreement)
    assert result['all_source_controls_ready'] is (not forex_disagreement)
    if forex_disagreement:
        row=result['inventory_mismatches'][0]
        assert (row['computed'],row['statement'],row['difference'])==('0','-50000','50000')
        assert row['control_source']=='Forex Transaction Report CSV'
        assert any('published opening quantity' in text for text in result['source_findings'])
        assert not any('Securities closing inventory does not reconcile' in text for text in result['source_findings'])
        assert 'Resolve the source and inventory findings' in result['publication_blocker']
    else:
        assert 'Monthly source checks passed' in result['publication_blocker']
    assert 'integration and database rehearsal' not in result['publication_blocker']
    assert result['futures']['closing_positions_confirmed_by_owner'] is True
    assert result['publication_authorized'] is result['publication_integration_ready'] is False
    assert repo.__dict__==before
    assert len(result['source_lineage']['removed_futures_row_ids'])==2


def test_deployment_publisher_blocks_futures_before_any_generation_write(deployed,monkeypatch,tmp_path):
    from tests.test_futures_accounting import write_source
    import campaigniq.account_deployment_publication as module
    c=deployed;repo=c.repositories[c.account]
    revision=repo.upload(c.draft.id,'thinkorswim_trade_history','futures.csv','text/csv',write_source(tmp_path).read_bytes(),replace=True)
    before=deepcopy(c.artifacts[c.account].rows)
    monkeypatch.setattr(module,'_publish_in_session',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Publication engine must not run')))
    with pytest.raises(PermissionError,match='Futures monthly publication is disabled'):
        c.publisher.publish(c.admin,c.account,c.draft.id,revision,confirm_publication=True,forex_applicable=False,crypto_applicable=False)
    assert c.artifacts[c.account].rows==before


@pytest.mark.parametrize('applicable',[True,False])
def test_crypto_activity_cannot_be_omitted_or_treated_as_ready_without_statement(flat,monkeypatch,applicable):
    c=flat;end_confirm(c);repo=c.repositories[c.account]
    import campaigniq.importers.thinkorswim.crypto_reader as reader
    monkeypatch.setattr(reader,'read_crypto_report',lambda *a,**k:dict(fills=[{}],holdings_snapshot={},
        cash_ledger=[],fill_control_reconciled=True,cash_control_reconciled=False))
    result=c.associated.futures_monthly_preview(c.admin,c.account,c.selected.id,repo.get(c.selected.id).revision,
        forex_applicable=False,crypto_applicable=applicable)
    assert result['crypto']['activity_present'] and not result['all_source_controls_ready']
    assert not result['publication_authorized']
    assert any('separate crypto account statement' in text for text in result['source_findings'])
    assert any('cash running-balance' in text for text in result['source_findings'])
    if not applicable:assert any('cannot be omitted' in text for text in result['source_findings'])


def test_monthly_preview_requires_current_revision(flat):
    c=flat
    with pytest.raises(ValueError,match='Draft changed'):
        c.associated.futures_monthly_preview(c.admin,c.account,c.selected.id,4)


def test_saved_futures_evidence_cannot_be_hidden_by_removing_history():
    from types import SimpleNamespace
    def document(key,role):
        if role==closing.ROLE:return 'ending.json','text/plain',b'{}'
        raise FileNotFoundError(role)
    session=SimpleNamespace(drafts=SimpleNamespace(document=document))
    assert monthly.has_futures(session,SimpleNamespace(id='draft'))
