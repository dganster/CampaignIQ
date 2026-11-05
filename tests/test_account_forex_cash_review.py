from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from campaigniq import account_forex_cash_review as m
from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
from campaigniq.account_deployment_application import DeploymentMonthlyApplication
from campaigniq.broker_account_binding import BrokerBinding
from campaigniq.import_draft_bridge import RestoredUpload
from tests import test_forex_cash_ledger_preview as support

@pytest.fixture
def review():
    helper=support.CashPreviewTests();helper.setUp()
    try:
        args=helper.args
        for k in ('cash_ledger','trade_history','supplemental_history'):
            p=args[k];p.write_text(p.read_text().replace('Account Statement for synthetic','Account Statement for 12345678'))
        p=args['transaction_report'];p.write_text(p.read_text()+'"Account (USD): 3607739, | Base currency: (USD)"\n')
        docs={m.HISTORY:('history.csv','text/csv',args['trade_history'].read_bytes()),m.REPORT:('report.csv','text/csv',p.read_bytes())}
        session=SimpleNamespace(account=SimpleNamespace(account_id='account-one'),drafts=SimpleNamespace(document=lambda id,role:docs[role]))
        service=SimpleNamespace(_known=lambda session:dict(securities=BrokerBinding.create('securities','12345678'),forex=BrokerBinding.create('forex','3607739')))
        draft=SimpleNamespace(id='draft-one',year=2026,month=10,revision=4)
        yield SimpleNamespace(service=service,session=session,draft=draft,cash=args['cash_ledger'].read_bytes(),supplement=args['supplemental_history'].read_bytes(),documents=docs)
    finally:helper.doCleanups()

def evaluate(c):return m.evaluate(c.service,c.session,c.draft,c.cash,c.supplement)

def test_real_review_scope_and_preserved_sources(review):
    c=review;before=dict(c.documents);r=evaluate(c)
    assert (r['account_id'],r['draft_id'],r['revision'])==('account-one','draft-one',4)
    assert r['source_headers_match_bindings'] is True
    assert r['calculation']['candidate_net_cash_change_usd']==-11
    assert r['broker_verified_cross_account_link'] is False
    assert all(r[k] is False for k in ('source_documents_saved','financial_state_written','publication_authorized'))
    assert c.documents==before

@pytest.mark.parametrize('field',['cash','supplement'])
def test_other_account_upload_rejected(review,field):
    setattr(review,field,getattr(review,field).replace(b'12345678',b'87654321'))
    with pytest.raises(ValueError,match='different broker account'):evaluate(review)

@pytest.mark.parametrize('role',[m.HISTORY,m.REPORT])
def test_saved_source_identity_rechecked(review,role):
    name,media,raw=review.documents[role]
    review.documents[role]=(name,media,raw.replace(b'12345678',b'87654321').replace(b'3607739',b'4607739'))
    with pytest.raises(ValueError,match='different broker account'):evaluate(review)

def test_wrong_month_rejected(review):
    review.draft.month=11
    with pytest.raises(ValueError):evaluate(review)

@pytest.mark.parametrize('media,raw',[('application/pdf',b'%PDF'),('text/csv',b''),('text/csv','string'),('text/csv',b'\xff')])
def test_invalid_upload_rejected(media,raw):
    with pytest.raises((ValueError,UnicodeError)):m.upload_bytes(SimpleNamespace(media_type=media,getvalue=lambda:raw))

def service_for(c):
    calls=[]
    @contextmanager
    def transaction(user,account_id,**kwargs):
        calls.append((user,account_id,kwargs))
        if user!='signed-in' or account_id!='account-one':raise PermissionError('Not authorized')
        yield c.session
    obj=DeploymentMonthlyInputs.__new__(DeploymentMonthlyInputs);obj.gateway=SimpleNamespace(transaction=transaction)
    obj._lock=lambda session,id:c.draft if id==c.draft.id else (_ for _ in ()).throw(PermissionError('Foreign draft'))
    obj._known=c.service._known
    return obj,calls

def run(c,obj,**kwargs):
    opts=dict(user='signed-in',account_id='account-one',draft_id='draft-one',revision=4,
        cash_upload=RestoredUpload('cash.csv','text/csv',c.cash),supplemental_upload=RestoredUpload('supp.csv','text/csv',c.supplement))
    opts.update(kwargs);return obj.review_forex_cash_sources(**opts)

def test_service_readonly_gateway(review):
    obj,calls=service_for(review);r=run(review,obj)
    assert calls==[('signed-in','account-one',{})]
    assert r['publication_authorized'] is False

@pytest.mark.parametrize('options',[{'user':'other'},{'account_id':'foreign'},{'draft_id':'foreign'},{'revision':3},{'revision':True}])
def test_foreign_and_stale_requests_rejected(review,options):
    obj,_=service_for(review)
    with pytest.raises((PermissionError,ValueError)):run(review,obj,**options)

def test_application_delegates_without_publication():
    inputs=SimpleNamespace(review_forex_cash_sources=Mock(return_value={'publication_authorized':False}))
    app=DeploymentMonthlyApplication.__new__(DeploymentMonthlyApplication);app._inputs=inputs
    assert app.review_forex_cash_sources('u','a','d',1,'x','y')=={'publication_authorized':False}
    inputs.review_forex_cash_sources.assert_called_once_with('u','a','d',1,'x','y')
