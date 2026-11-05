"""Regression for the rehearsal's actual reviewed input-role privileges."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
import psycopg
import pytest

from campaigniq.account_forex_cash_sources_rehearsal import _monetary_approval_fault_upload
from campaigniq.account_forex_financial_policy import ROLE
from campaigniq.account_runtime_roles import POLICY
from campaigniq.persistence.account_data_session import AccountTransactionFailed
from tests.test_forex_financial_policy import ready, confirmed, sources, saving, bound_context, context, repo, revision, review


@pytest.mark.parametrize('failure,expected,message',[
    ('after_receipt',RuntimeError,'Injected monetary approval'),
    ('changed_receipt',ValueError,'post-write verification'),
    ('forbidden_sql',AccountTransactionFailed,'transaction failed')])
def test_each_rehearsal_fault_has_its_own_error_and_rolls_back_approval(ready,monkeypatch,failure,expected,message):
    c=ready;store=repo(c);digest=review(c)['artifact_sha256'];rev=revision(c)
    before=deepcopy((store.rows,store.docs,store.originals,c.artifacts[c.account].rows))
    allowed={(schema,table):set(privileges) for schema,table,privileges in POLICY}
    assert 'UPDATE' in allowed['campaigniq_accounts','import_draft_documents']
    assert 'UPDATE' not in allowed['campaigniq_accounts','artifact_storage_dev']
    aborted=False;queries=[];selected=None
    class Connection:
        def execute(self,sql,params=None):
            nonlocal aborted,selected
            queries.append(sql)
            if sql.startswith('SELECT id,content'):
                did,role=params;selected=((did,role),store.docs[did,role][2]);return self
            if sql.startswith('UPDATE campaigniq_accounts.import_draft_documents'):
                _,raw,key=params;name,media,_=store.docs[key];store.docs[key]=(name,media,raw);return self
            assert sql.startswith('UPDATE campaigniq_accounts.artifact_storage_dev')
            aborted=True
            raise psycopg.errors.InsufficientPrivilege('Input role cannot update financial artifacts')
        def fetchone(self):return selected
    @contextmanager
    def operation():yield Connection()
    store._session=SimpleNamespace(_operation=operation)
    original_get=store.get;original_upload=store.upload
    def checked_get(*args):
        if aborted:raise AccountTransactionFailed('Account transaction failed and must roll back')
        return original_get(*args)
    monkeypatch.setattr(store,'get',checked_get)
    reached=[]
    injected=_monetary_approval_fault_upload(lambda repository,*args,**kwargs:original_upload(*args,**kwargs),failure,reached)
    monkeypatch.setattr(store,'upload',lambda *args,**kwargs:injected(store,*args,**kwargs))
    with pytest.raises(expected,match=message):
        c.associated.save_forex_financial_policy(c.admin,c.account,c.selected.id,rev,
            reviewed_candidate_sha256=digest,confirm_daily_basis=True,
            confirm_retained_differences=True,confirm_coverage_limits=True)
    assert reached==[failure]
    assert (repo(c).rows,repo(c).docs,repo(c).originals,c.artifacts[c.account].rows)==before
    assert (c.selected.id,ROLE) not in repo(c).docs
    if failure=='changed_receipt':assert any('UPDATE campaigniq_accounts.import_draft_documents' in q for q in queries)
    if failure=='forbidden_sql':assert any('UPDATE campaigniq_accounts.artifact_storage_dev' in q for q in queries)
