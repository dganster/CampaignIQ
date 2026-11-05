from contextlib import contextmanager
from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from campaigniq import account_forex_cash_review as cash
from campaigniq import account_forex_inventory_preview as inventory
from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
from campaigniq.broker_account_binding import BrokerBinding
from campaigniq.crypto_account_rehearsal_sources import pdf
from campaigniq.forex_cash_rehearsal_sources import sources
from campaigniq.forex_inventory_rehearsal_sources import monthly_upload, predecessor, statement
from campaigniq.persistence.monthly_publication import PROTOCOL_FILE, _serialize_publication_protocol, _serialize_finalized_month_marker

class Storage:
    def __init__(self):
        self.rows={'2026-09-lot-book.json':predecessor(),
            PROTOCOL_FILE:_serialize_publication_protocol(first_period_end=date(2026,9,30)),
            '2026-09-finalized.json':_serialize_finalized_month_marker(period_end=date(2026,9,30))}
    def exists(self,key):return key in self.rows
    def read_text(self,key):return self.rows[key]
    def write_text(self,*a):raise AssertionError('Read-only preview must not write')
    def delete(self,*a):raise AssertionError('Read-only preview must not delete')

@pytest.fixture
def context():
    uploads,ledger,supplement=sources()
    docs={role:(u.name,u.media_type,u.content) for role,u in uploads.items()}
    known={'securities':BrokerBinding.create('securities','24681357'),'forex':BrokerBinding.create('forex','8642097')}
    def document(draft_id,role):
        if role not in docs:raise FileNotFoundError(role)
        return docs[role]
    session=SimpleNamespace(account=SimpleNamespace(account_id='account'),drafts=SimpleNamespace(document=document),artifacts=Storage())
    service=SimpleNamespace(_known=lambda session:known)
    draft=SimpleNamespace(id='draft',year=2026,month=10,revision=2)
    review=cash.evaluate(service,session,draft,ledger.content,supplement.content)
    receipt=cash.make_receipt(session,draft,'owner',cash.context(service,session,review))
    docs.update({cash.CASH:('cash.csv','text/csv',ledger.content),cash.SUPPLEMENT:('supp.csv','text/csv',supplement.content),cash.RECEIPT:('receipt.json','text/plain',cash.canonical(receipt))})
    draft.revision=5
    return SimpleNamespace(service=service,session=session,draft=draft,docs=docs,known=known)

def run(c,text=None):return inventory.evaluate(c.service,c.session,c.draft,monthly_upload().content if text is None else pdf(text))

def test_actual_pdf_authoritative_replay_and_exact_originals(context):
    c=context;before=deepcopy((c.docs,c.session.artifacts.rows));result=run(c)
    assert result['statement_opening_quantities']=={'USD/JPY':'-50000'} and result['statement_closing_quantities']=={}
    assert result['authoritative_opening_matches'] and result['authoritative_replay_closing_matches']
    assert result['preview_close_allocations']==[dict(reference='1001',lot_id='synthetic-forex-opening',campaign_id='synthetic-existing-forex-campaign',quantity='50000')]
    assert result['original_cash_review']['calculation']['csv_mtd_minus_candidate_usd']==Decimal('1')
    assert result['source_row_origins']==[dict(section_data_row=2,reference='1001')]
    assert result['preview_ending_lots']==[]
    assert all(result[f] is False for f in ('monthly_pdf_saved','financial_state_written','publication_authorized','broker_verified_cross_account_link'))
    assert '8642097' not in json.dumps(result,default=str)
    assert (c.docs,c.session.artifacts.rows)==before and run(c)==result

@pytest.mark.parametrize('missing',['2026-09-lot-book.json','2026-09-finalized.json'])
def test_missing_predecessor_never_seeds_lots(context,missing):
    del context.session.artifacts.rows[missing];r=run(context)
    assert not r['authoritative_predecessor_available'] and not r['authoritative_opening_matches'] and not r['authoritative_replay_closing_matches']
    assert r['preview_close_allocations']==[] and r['preview_ending_lots']==[] and r['statement_inventory_equation_verified']
    assert 'no opening lots' in r['blockers'][0]

@pytest.mark.parametrize('key,field,value',[
    ('2026-09-finalized.json','period_end','2026-08-31'),('2026-09-finalized.json','version',True),
    (PROTOCOL_FILE,'format','wrong'),('2026-09-lot-book.json','period_end','2026-08-31')])
def test_corrupt_publication_controls(context,key,field,value):
    b=json.loads(context.session.artifacts.rows[key]);b[field]=value;context.session.artifacts.rows[key]=json.dumps(b)
    before=deepcopy(context.session.artifacts.rows)
    with pytest.raises(ValueError):run(context)
    assert context.session.artifacts.rows==before

@pytest.mark.parametrize('change',['quantity','nonfinite','duplicate','future','flat'])
def test_authoritative_opening_cannot_be_overwritten(context,change):
    key='2026-09-lot-book.json';b=json.loads(context.session.artifacts.rows[key])
    if change=='quantity':b['lots'][0]['quantity']='-40000'
    elif change=='nonfinite':b['lots'][0]['quantity']='NaN'
    elif change=='duplicate':b['lots'].append(b['lots'][0].copy())
    elif change=='future':b['lots'][0]['opened_at']='2026-10-01T00:00:00'
    else:b['lots']=[]
    context.session.artifacts.rows[key]=json.dumps(b);before=deepcopy(context.session.artifacts.rows)
    with pytest.raises(ValueError):run(context)
    assert context.session.artifacts.rows==before

@pytest.mark.parametrize('old,new',[
    ('8642097 USD','8642098 USD'),('October 30, 2026','September 30, 2026'),('(CST)','(EST)'),
    ('Thursday, October 1, 2026','Friday, October 2, 2026'),('9/25/2026','10/1/2026'),
    ('Sell (50,000) 157.2060000','Sell (49,999) 157.2060000'),('Sell (50,000) ($69.49)','Sell (49,999) ($69.49)'),
    ('B CLOSE 50,000','B OPEN 50,000'),('158.505 1001','158.506 1001'),('158.505 1001','158.505 1002'),
    ('Currency Pair Buy/Sell Quantity Trade Price','Missing heading'),('Floating Profit/Loss Net USD :','Missing footer:'),
    ('Open Position(s) at End of Month','Missing closing section'),('TR Details:','Missing detail:')])
def test_wrong_monthly_evidence_rejected(context,old,new):
    with pytest.raises(ValueError):run(context,statement().replace(old,new))

def test_foreign_daily_report_is_not_hidden_by_matching_summary(context):
    text=statement();i=text.rfind('8642097 USD');text=text[:i]+text[i:].replace('8642097 USD','8642098 USD',1)
    with pytest.raises(ValueError,match='different broker account'):run(context,text)

def test_conflicting_closing_is_not_replaced_by_replay(context):
    text=statement().replace('Floating Profit/Loss Net USD :',
        'USD/JPY\nTR Details: 9/25/2026 3:47:16 PM Sell (50,000) 157.2060000\nUSD/JPY - TOTAL POSITION: Sell (50,000) ($69.49) $2500\nFloating Profit/Loss Net USD :',1)
    with pytest.raises(ValueError,match='closing positions'):run(context,text)

def test_extra_and_duplicate_executions_rejected(context):
    row='2026-10-06 21:04:08 USD/JPY STOP B CLOSE 50,000 158.505 1001 $0.00'
    for extra in (row,row.replace('1001','1009')):
        with pytest.raises(ValueError):run(context,statement().replace(row,row+'\n'+extra))

@pytest.mark.parametrize('role',[cash.CASH,cash.SUPPLEMENT,cash.REPORT,cash.HISTORY,cash.RECEIPT])
def test_changed_saved_original_withholds_inventory(context,role):
    name,media,raw=context.docs[role]
    if role==cash.RECEIPT:
        body=json.loads(raw);body['review_sha256']='0'*64;raw=cash.canonical(body)
    else:raw+=b'\n'
    context.docs[role]=(name,media,raw)
    with pytest.raises(ValueError):run(context)

@pytest.mark.parametrize('raw,media',[(b'bad','application/pdf'),(b'%PDF-','text/plain'),('%PDF-','application/pdf')])
def test_invalid_pdf_before_connection(raw,media):
    with pytest.raises(ValueError):inventory.pdf_bytes(SimpleNamespace(media_type=media,getvalue=lambda:raw))

def service(c):
    calls=[]
    @contextmanager
    def transaction(user,account,**kwargs):
        calls.append((user,account,kwargs))
        if user!='owner' or account!='account':raise PermissionError('Foreign scope')
        yield c.session
    obj=object.__new__(DeploymentMonthlyInputs);obj.gateway=SimpleNamespace(transaction=transaction);obj._known=c.service._known
    def lock(session,draft):
        if draft!=c.draft.id:raise FileNotFoundError('Foreign draft')
        return c.draft
    obj._lock=lock;return obj,calls

def test_readonly_gateway(context):
    obj,calls=service(context);r=obj.forex_inventory_preview('owner','account','draft',5,monthly_upload())
    assert r['authoritative_opening_matches'] and calls==[('owner','account',{})]

@pytest.mark.parametrize('args',[('foreign','account','draft',5),('owner','foreign','draft',5),('owner','account','foreign',5),('owner','account','draft',4),('owner','account','draft',True)])
def test_foreign_stale_service(context,args):
    obj,_=service(context)
    with pytest.raises((ValueError,PermissionError,FileNotFoundError)):obj.forex_inventory_preview(*args,monthly_upload())

def test_application_delegation():
    from campaigniq.account_deployment_application import DeploymentMonthlyApplication
    app=object.__new__(DeploymentMonthlyApplication);app._inputs=SimpleNamespace(forex_inventory_preview=Mock(return_value='review'))
    assert app.forex_inventory_preview('u','a','d',2,'pdf')=='review'
    app._inputs.forex_inventory_preview.assert_called_once_with('u','a','d',2,'pdf')
