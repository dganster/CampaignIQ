from types import SimpleNamespace
from campaigniq.ui.account_crypto_sources_view import render


class UI:
    def __init__(self,*,click='',uploads=True,confirm=False):
        self.click=click;self.uploads=uploads;self.confirm=confirm;self.session_state={};self.calls=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def expander(self,*a,**k):return self
    def form(self,*a,**k):return self
    def caption(self,*a):pass
    def warning(self,*a):pass
    def json(self,*a):pass
    def dataframe(self,*a,**k):pass
    def success(self,*a):self.calls.append('success')
    def error(self,*a):self.calls.append('error')
    def file_uploader(self,label,**kw):
        return SimpleNamespace(name=label+'.pdf',type='application/pdf',getvalue=lambda:b'%PDF-synthetic') if self.uploads else None
    def button(self,label,**kw):self.calls.append(label);return self.click==label
    def checkbox(self,*a,**kw):return self.confirm
    def form_submit_button(self,label):self.calls.append(label);return self.click==label
    def rerun(self):self.calls.append('rerun')


def calculation():
    fields=['documented_opening_cost_usd','purchases_including_fees_usd','fees_already_in_trade_cash_usd',
        'full_ending_cost_basis_usd','proposed_realized_sales_pnl_usd','proposed_ending_cash_usd']
    return dict(totals=dict.fromkeys(fields,'0'),review={'original_discrepancies_resolved':False})


def screen(ui,*,writable=True,published=False):
    calls=[]
    def review(*a):calls.append('review');return dict(calculation=calculation(),reviewed_preview_sha256='a'*64)
    def save(*a,**kw):calls.append(('save',kw))
    service=SimpleNamespace(review_crypto_statements=review,save_crypto_statement_sources=save,
        crypto_statement_preview=lambda *a:dict(calculation=calculation()))
    render(ui,service,'actor','account',SimpleNamespace(id='draft',revision=7),key='revision-7',writable=writable,published=published)
    return calls


def test_missing_files_never_call_review_or_save():
    ui=UI(click='Review crypto sources',uploads=False)
    assert screen(ui)==[] and 'error' in ui.calls


def test_review_does_not_save_and_confirmation_is_passed_explicitly():
    ui=UI(click='Review crypto sources');assert screen(ui)==['review']
    assert ui.session_state['revision-7:crypto-reviewed']['reviewed_preview_sha256']=='a'*64
    ui.click='Save reviewed crypto sources';assert screen(ui)==[]
    assert 'error' in ui.calls
    ui.confirm=True;calls=screen(ui)
    assert calls[0][1]['confirm_same_crypto_account'] is True
    assert calls[0][1]['confirm_source_choices'] is True
    assert calls[0][1]['reviewed_preview_sha256']=='a'*64



def test_readonly_and_published_screens_have_no_upload_or_approval_controls():
    for kwargs in ({'writable':False},{'published':True}):
        ui=UI(click='Save reviewed crypto sources');assert screen(ui,**kwargs)==[]
        assert 'Save reviewed crypto sources' not in ui.calls
        assert 'View saved crypto source review' in ui.calls


def test_new_revision_does_not_inherit_previous_review():
    ui=UI(click='Save reviewed crypto sources');ui.session_state['revision-6:crypto-reviewed']={}
    assert screen(ui)==[] and 'Save reviewed crypto sources' not in ui.calls


def test_changed_pdf_clears_previous_review_before_save():
    ui=UI(click='Review crypto sources');screen(ui)
    ui.click='Save reviewed crypto sources';ui.confirm=True
    ui.file_uploader=lambda *a,**k:SimpleNamespace(name='changed.pdf',type='application/pdf',getvalue=lambda:b'%PDF-changed')
    assert screen(ui)==[] and 'revision-7:crypto-reviewed' not in ui.session_state


def test_real_data_screen_exposes_review_and_requires_both_pdfs():
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    text=Path(__file__).with_name('test_account_signedin_inputs_view.py').read_text()
    code=text.split("    code = '''",1)[1].split("'''",1)[0]
    for key,value in (('READY',True),('WRITABLE',True),('ADMIN',True),('DRAFT',True),('ACCOUNTS',True),('MISMATCH',False),('PUBLISHED',False)):
        code=code.replace(key,str(value))
    code=code.replace('class Service:\n','class Service:\n    multi_asset_enabled=True\n')
    code=code.replace('render(st,Service(),USER)','render(st,Service(),USER,deployment=True)')
    app=AppTest.from_string(code).run(timeout=20)
    assert not app.exception
    assert 'schwab_crypto_statement' not in next(s for s in app.selectbox if s.label=='Document role').options
    assert len(app.get('file_uploader'))==3 # Ordinary document plus two crypto PDFs.
    next(b for b in app.button if b.label=='Review crypto sources').click().run(timeout=20)
    assert not app.exception and app.session_state['calls']==[]
    assert any('both monthly crypto statement PDFs' in e.value for e in app.error)
