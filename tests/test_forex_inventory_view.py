from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
from campaigniq.ui.account_forex_inventory_view import render
from campaigniq.forex_daily_rehearsal_sources import upload

class Screen:
    def __init__(self):
        self.session_state={};self.upload=upload();self.click=True;self.tables=[];self.errors=[];self.warnings=[];self.successes=[]
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

class DailySourceScreen(Screen):
    def __init__(self):
        super().__init__();self.button_name='Validate Forex opening and closing positions';self.reruns=0
    def button(self,label,**kwargs):return label==self.button_name and not kwargs.get('disabled',False)
    def checkbox(self,*args,**kwargs):return True
    def rerun(self):self.reruns+=1

def source_setup():
    st=DailySourceScreen();r=result();r['review_sha256']='a'*64
    service=SimpleNamespace(forex_daily_inventory_preview=Mock(return_value=r),save_forex_daily_sources=Mock(),forex_daily_source_preview=Mock(return_value=r))
    return st,service

def test_daily_save_uses_exact_review_and_explicit_choices():
    st,s=source_setup();draw(st,s);st.button_name='Save daily Forex source confirmation';draw(st,s)
    assert s.save_forex_daily_sources.call_args.kwargs==dict(reviewed_preview_sha256='a'*64,confirm_same_account=True,confirm_coverage_limits=True,replace=True)
    assert st.reruns==1 and not st.session_state

def test_daily_changed_revision_account_or_archive_clears_confirmation():
    for case in ('revision','account','archive'):
        st,s=source_setup();draw(st,s);st.button_name='Save daily Forex source confirmation'
        if case=='archive':st.upload=upload(complete=True)
        draw(st,s,6 if case=='revision' else 5,'other' if case=='account' else 'account')
        s.save_forex_daily_sources.assert_not_called();assert not st.session_state

def test_failed_fresh_daily_review_discards_old_confirmation():
    st,s=source_setup();draw(st,s);s.forex_daily_inventory_preview.side_effect=ValueError('Changed source')
    draw(st,s);assert not st.session_state and st.errors==['Changed source']

def test_view_only_and_published_daily_reviews_have_no_save_controls():
    for flags in ({'writable':False},{'published':True}):
        st,s=source_setup();draw(st,s);st.button_name='Save daily Forex source confirmation'
        render(st,s,'owner','account',SimpleNamespace(id='draft',revision=5),key='scope',**flags)
        s.save_forex_daily_sources.assert_not_called()

def test_saved_daily_review_does_not_require_reupload():
    st,s=source_setup();st.upload=None;st.button_name='View saved daily Forex source review';draw(st,s)
    s.forex_daily_source_preview.assert_called_once_with('owner','account','draft',5)
    s.forex_daily_inventory_preview.assert_not_called()


def test_financial_candidate_uses_saved_sources_without_upload_or_save():
    st=DailySourceScreen();st.upload=None;st.button_name='Calculate saved Forex financial candidate'
    calc=dict(totals={'trading_usd':'-10.00'},closes=[{'reference':'1001','candidate_pl_usd':'-10.00'}],
        last_independent_daily_boundary='2026-10-07',all_original_financing_rows_classified=True,
        limitations=['Policy approval and writer integration remain pending.'])
    s=SimpleNamespace(forex_financial_candidate_preview=Mock(return_value={'artifact':{'calculation':calc}}))
    draw(st,s)
    s.forex_financial_candidate_preview.assert_called_once_with('owner','account','draft',5)
    assert len(st.tables)==2 and st.successes
    assert st.warnings==['Policy approval and writer integration remain pending.']
    st.tables=[];st.successes=[];s.forex_financial_candidate_preview.side_effect=ValueError('Changed receipt')
    draw(st,s)
    assert not st.tables and not st.successes and st.errors==['Changed receipt']
