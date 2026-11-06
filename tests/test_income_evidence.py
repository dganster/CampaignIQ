from copy import deepcopy
from decimal import Decimal as D
from hashlib import sha256
import json
import pytest
from campaigniq.account_income_evidence import legacy_statements,read_bound_statement
from campaigniq.broker_account_binding import BrokerBinding
from campaigniq.income_rehearsal_sources import controls
from campaigniq.account_dashboard import ReportSnapshot
from campaigniq.dashboard_sources import load_dashboard_sources
from campaigniq.import_draft_bridge import RestoredUpload
from tests.test_account_deployment_publication import deployed,local,month,bound_context,context,FIXTURES,publish
from tests.test_account_local_publication import MemoryStorage

def statement():return (FIXTURES/'account-month-closing.txt').read_text()+controls()
def legacy(raw=None):
    raw=statement().encode() if raw is None else raw;store=MemoryStorage('synthetic')
    manifest=dict(format='campaigniq.monthly_import_provenance',version=1,period_start='2025-01-01',period_end='2025-01-31',inputs=[dict(role='schwab_closing_position_snapshot',sha256=sha256(raw).hexdigest(),byte_size=len(raw))])
    store.rows['2025-01-import-provenance.json']=json.dumps(manifest)
    return store,[('synthetic',sha256(raw).hexdigest(),raw,'text/plain',2025,1)],{'securities':BrokerBinding.create('securities','12345678')}

def test_legacy_income_bound_to_published_provenance_and_duplicates_not_double_counted():
    store,rows,known=legacy();before=deepcopy(store.rows)
    result=legacy_statements(store,rows,known,('2025-01',))[0]
    assert result.status=='verified' and result.income.net_income==D('56.75')
    assert result.original_sha256==rows[0][1] and store.rows==before
    twice=legacy_statements(store,rows+rows,known,('2025-01',))
    assert len(twice)==1 and twice[0].income.net_income==D('56.75')

@pytest.mark.parametrize('case',['missing','digest','provenance','period','identity','layout'])
def test_unverified_legacy_income_unknown_never_zero_no_repair(case):
    store,rows,known=legacy()
    if case=='missing':rows=[]
    elif case=='digest':rows=[('synthetic','0'*64,rows[0][2],'text/plain',2025,1)]
    elif case=='provenance':store.rows['2025-01-import-provenance.json']='{}'
    elif case=='period':rows=[(*rows[0][:-2],2025,2)]
    elif case=='identity':known={'securities':BrokerBinding.create('securities','87654321')}
    elif case=='layout':store,rows,known=legacy((FIXTURES/'account-month-closing.txt').read_bytes())
    before=deepcopy(store.rows);result=legacy_statements(store,rows,known,('2025-01',))[0]
    assert result.status=='unverified' and result.income is None and store.rows==before

def test_trading_month_results_and_dashboard_include_income_without_changing_artifacts(deployed):
    c=deployed;repo=c.repositories[c.account]
    text=(FIXTURES/'account-month-closing.txt').read_text()+controls(deposits='0.00',withdrawals='0.00',sales='0.00')
    text=text.replace('$11,000.00 $10,000.00','$11,056.75 $11,000.00')
    c.revision=c.bound.save(c.admin,c.account,c.draft.id,c.revision,'schwab_brokerage_statement',RestoredUpload('statement.txt','text/plain',text.encode()),replace=True)
    publish(c);before=deepcopy(c.artifacts[c.account].rows);docs=deepcopy(repo.docs)
    from campaigniq.account_deployment_results import _deployment_results_session
    with c.bound.gateway.transaction(c.admin,c.account) as session:
        result=_deployment_results_session(c.publisher.inputs,session,session.drafts.get(c.draft.id),c.account,c.revision)
    assert result.income_evidence.status=='verified' and result.income_evidence.income.net_income==D('56.75')
    summaries,_,_=load_dashboard_sources(ReportSnapshot(c.account,list(before.items()),income=(result.income_evidence,)))
    summary=summaries[0]
    assert summary.realized_plus_statement_income==D('56.75') and summary.combined_realized_pnl==0
    assert summary.statement_income.account_investment_result==D('56.75')
    assert c.artifacts[c.account].rows==before and repo.docs==docs
    missing,_,_=load_dashboard_sources(ReportSnapshot(c.account,list(before.items())))
    assert missing[0].realized_plus_statement_income is None and missing[0].combined_realized_pnl==0

def test_income_metadata_cannot_be_mixed_with_different_published_snapshot(deployed):
    c=deployed;publish(c);store=c.artifacts[c.account]
    item=read_bound_statement('2025-01',statement().encode(),'text/plain',{'securities':BrokerBinding.create('securities','12345678')},published_binding='0'*64)
    with pytest.raises(ValueError):load_dashboard_sources(ReportSnapshot(c.account,list(store.rows.items()),income=(item,)))
    with pytest.raises(ValueError):load_dashboard_sources(ReportSnapshot(c.account,list(store.rows.items()),income=(item,item)))

def test_missing_income_layout_does_not_invent_zero(deployed):
    c=deployed;publish(c);before=deepcopy(c.artifacts[c.account].rows)
    from campaigniq.account_deployment_results import _deployment_results_session
    with c.bound.gateway.transaction(c.admin,c.account) as session:
        result=_deployment_results_session(c.publisher.inputs,session,session.drafts.get(c.draft.id),c.account,c.revision)
    assert result.income_evidence.status=='unverified' and result.income_evidence.income is None
    assert c.artifacts[c.account].rows==before
