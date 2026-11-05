from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
from campaigniq.ui.account_forex_inventory_view import render
from campaigniq.forex_daily_rehearsal_sources import upload

class Screen:
    def __init__(self):
        self.upload=upload();self.click=True;self.tables=[];self.errors=[];self.warnings=[];self.successes=[]
    def caption(self,*a,**k):pass
    def file_uploader(self,*a,**k):return self.upload
    def button(self,*a,**k):return self.click and not k.get('disabled')
    def table(self,value):self.tables.append(value)
    def success(self,value):self.successes.append(value)
    def warning(self,value):self.warnings.append(value)
    def error(self,value):self.errors.append(value)
    def expander(self,*a,**k):return nullcontext()
    def json(self,*a,**k):pass

def result():return dict(statement_opening_quantities={'USD/JPY':'-50000'},last_daily_closing_quantities={},
    combined_inventory_evidence_reconciled=True,last_independent_daily_boundary='2026-10-07',export_month_end_date='2026-10-31',export_month_end_cash_usd='989.00',blockers=['Publication remains blocked.'])
def draw(st,service,revision=5,account='account'):
    render(st,service,'owner',account,SimpleNamespace(id='draft',revision=revision),key='scope')

def test_one_shot_checks_current_scope_without_approval_or_save_controls():
    st=Screen();service=SimpleNamespace(forex_daily_inventory_preview=Mock(return_value=result()))
    draw(st,service)
    args=service.forex_daily_inventory_preview.call_args.args
    assert args[:4]==('owner','account','draft',5) and args[4].content==st.upload.content
    assert len(st.tables)==1 and st.tables[0][-1]['Quantity']=='0'
    assert st.successes and st.warnings==['Publication remains blocked.']
    # A rerun after changing account/revision or PDF cannot redisplay an old review.
    st.click=False;st.tables=[];st.successes=[];st.upload=None
    draw(st,service,6,'other')
    assert not st.tables and not st.successes and service.forex_daily_inventory_preview.call_count==1

def test_missing_pdf_does_not_call_service():
    st=Screen();st.upload=None;service=SimpleNamespace(forex_daily_inventory_preview=Mock())
    draw(st,service);service.forex_daily_inventory_preview.assert_not_called()

def test_failure_does_not_present_old_success():
    st=Screen();service=SimpleNamespace(forex_daily_inventory_preview=Mock(side_effect=ValueError('Wrong month')))
    draw(st,service);assert st.errors==['Wrong month'] and not st.successes and not st.tables

def test_missing_predecessor_shows_incomplete_verification():
    st=Screen();r=result();r.update(combined_inventory_evidence_reconciled=False)
    draw(st,SimpleNamespace(forex_daily_inventory_preview=Mock(return_value=r)))
    assert not st.successes and 'incomplete' in st.warnings[0]
