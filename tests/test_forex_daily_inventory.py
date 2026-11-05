from copy import deepcopy
from datetime import date
from hashlib import sha256
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile
import pytest
from campaigniq import account_forex_daily_inventory as daily
from campaigniq.forex_daily_inventory import validate_daily_chain, read_daily
from campaigniq.forex_daily_rehearsal_sources import reports, upload
from campaigniq.forex_inventory_rehearsal_sources import predecessor
from campaigniq.broker_account_binding import BrokerBinding
from campaigniq.persistence.lot_book_store import deserialize_lot_book
from tests.test_account_forex_inventory_preview import context, service

KNOWN={'forex':BrokerBinding.create('forex','8642097')}
def bundle(items):return [dict(name=n,text=t,pdf_sha256=sha256(t.encode()).hexdigest()) for n,t in items]
def validate(items=None,**kwargs):return validate_daily_chain(bundle(items or reports()),known=KNOWN,period_start=date(2026,10,1),period_end=date(2026,10,31),**kwargs)

def test_daily_chain_without_monthly_statement_and_partial_boundary_explicit():
    r=validate()
    assert r['daily_report_count']==5 and r['execution_count']==1
    assert r['last_independent_daily_boundary']=='2026-10-07' and not r['full_independent_daily_coverage']
    assert r['statement_opening_quantities']=={'USD/JPY':'-50000'} and r['last_daily_closing_quantities']=={}
    assert r['daily_settled_pl_usd']=='-10.00' and r['daily_financing_usd']=='-1.00'
    assert not r['authoritative_opening_matches'] and not r['publication_authorized']

def test_complete_daily_chain_has_exact_final_business_day():
    r=validate(reports(complete=True));assert r['full_independent_daily_coverage'] and r['last_independent_daily_boundary']=='2026-10-30'
    assert r['dates_without_supplied_daily_reports']==[]

def test_actual_authoritative_lot_allocation_is_preserved():
    book=deserialize_lot_book(predecessor()).lot_book
    r=validate(opening_book=book)
    assert r['authoritative_opening_matches']
    assert r['preview_close_allocations']==[dict(reference='1001',lot_id='synthetic-forex-opening',campaign_id='synthetic-existing-forex-campaign',quantity='50000')]
    assert book.lots(book.instruments()[0])[0].quantity==-50000

@pytest.mark.parametrize('change',['missing_first','missing_middle','duplicate','gap','cash_chain','position','effect','finance','identity','extra_execution'])
def test_daily_evidence_conflicts_rejected(change):
    items=reports()
    if change=='missing_first':items=items[1:]
    elif change=='missing_middle':items=items[:2]+items[3:]
    elif change=='duplicate':items.append(items[-1])
    else:
        index=1 if change=='cash_chain' else -1
        name,text=items[index]
        if change=='gap':text=text.replace('Tuesday, October 6, 2026','Monday, October 5, 2026')
        elif change=='cash_chain':text=text.replace('1000.00','1001.00')
        elif change=='position':text=text.replace('Floating Profit/Loss Net USD :','USD/JPY\nTR Details: 9/25/2026 3:47:16 PM Sell (50,000) 157.206\nUSD/JPY - TOTAL POSITION: Sell (50,000) $0.00\nFloating Profit/Loss Net USD :')
        elif change=='effect':text=text.replace('B CLOSE','B OPEN')
        elif change=='finance':text=text.replace('$0.01 ($1.00)','$0.01 ($2.00)')
        elif change=='identity':text=text.replace('8642097 USD','8642098 USD')
        elif change=='extra_execution':text=text.replace('Financing Report','2026-10-06 21:04:08 USD/JPY STOP B CLOSE 50,000 158.505 1001 $0.00\nFinancing Report')
        items[index]=(name,text)
    with pytest.raises(ValueError):validate(items)

def test_daily_layout_account_panel_is_separate_from_cash_table():
    text=reports()[0][1]
    old='Account Account Currency\n8642097 USD'
    replacement='Floating Profit/Loss $0.00                 Account       Account Currency\nEnding Cash Balance $1000.00                  8642097             USD'
    # Header column is authoritative: aligned account identifier accepted,
    # identifiers in unrelated cash/phone fields never count.
    lines=replacement.splitlines();column=lines[0].index('Account')
    lines[1]='Ending Cash Balance $1000.00'.ljust(column)+'8642097 USD'
    # Avoid duplicate financial control lines in this synthetic layout check.
    changed=text.replace('Ending Cash Balance $1000.00\n','').replace('Floating Profit/Loss $0.00\n','').replace(old,'\n'.join(lines))
    assert read_daily(changed,known=KNOWN)['positions']=={'USD/JPY':-50000}
    with pytest.raises(ValueError):read_daily(changed.replace('8642097 USD','8642098 USD'),known=KNOWN)

def run(c,raw=None):return daily.evaluate(c.service,c.session,c.draft,upload().content if raw is None else raw)

def test_account_archive_review_binds_saved_sources_and_tail_without_writes(context):
    c=context;before=deepcopy((c.docs,c.session.artifacts.rows));r=run(c)
    assert r['saved_execution_facts_verified'] and r['combined_inventory_evidence_reconciled']
    assert not r['independent_month_end_positions_verified'] and r['ledger_tail_no_activity_recorded']
    assert r['export_supported_ending_quantities']=={} and r['export_month_end_cash_usd']=='989.00'
    assert r['original_csv_mtd_minus_daily_settled_usd']=='1.00'
    assert r['execution_clock_observations'][0]['observed_seconds']==7200
    assert not r['daily_pdfs_saved'] and not r['publication_authorized']
    assert (c.docs,c.session.artifacts.rows)==before and run(c)==r
    assert '8642097' not in json.dumps(r,default=str)

def test_account_complete_daily_archive(context):
    r=run(context,upload(complete=True).content)
    assert r['independent_month_end_positions_verified'] and r['full_independent_daily_coverage']

def test_account_missing_predecessor_remains_incomplete(context):
    del context.session.artifacts.rows['2026-09-finalized.json'];r=run(context)
    assert not r['authoritative_opening_matches'] and not r['combined_inventory_evidence_reconciled']
    assert r['preview_close_allocations']==[]

@pytest.mark.parametrize('name,content',[('../outside.pdf',b'%PDF-'),('/outside.pdf',b'%PDF-'),('not.pdf',b'bad'),('wrong.csv',b'csv')])
def test_archive_entry_validation(name,content):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:z.writestr(name,content)
    with pytest.raises(ValueError):daily.originals(out.getvalue())

def test_duplicate_archive_and_invalid_bytes_rejected():
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('same.pdf',b'%PDF-1')
        with pytest.warns(UserWarning):z.writestr('same.pdf',b'%PDF-2')
    with pytest.raises(ValueError):daily.originals(out.getvalue())
    with pytest.raises(ValueError):daily.originals(b'bad')
    with pytest.raises(ValueError):daily.archive_bytes(SimpleNamespace(media_type='text/plain',getvalue=lambda:upload().content))

def test_daily_service_readonly_and_current_revision(context):
    obj,calls=service(context)
    r=obj.forex_daily_inventory_preview('owner','account','draft',5,upload())
    assert r['combined_inventory_evidence_reconciled'] and calls==[('owner','account',{})]
    for args in [('foreign','account','draft',5),('owner','account','foreign',5),('owner','account','draft',4),('owner','account','draft',True)]:
        with pytest.raises((ValueError,PermissionError,FileNotFoundError)):obj.forex_daily_inventory_preview(*args,upload())

def test_daily_application_delegation_without_publication():
    from campaigniq.account_deployment_application import DeploymentMonthlyApplication
    app=object.__new__(DeploymentMonthlyApplication);app._inputs=SimpleNamespace(forex_daily_inventory_preview=Mock(return_value='review'))
    assert app.forex_daily_inventory_preview('u','a','d',1,'zip')=='review'
