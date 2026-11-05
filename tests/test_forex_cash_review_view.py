from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.ui.account_forex_cash_review_view import render

class Screen:
    def __init__(self,button=None):
        self.session_state={};self.button_name=button;self.buttons=[];self.errors=[];self.reruns=0
        self.uploads=[RestoredUpload('cash.csv','text/csv',b'cash'),RestoredUpload('supp.csv','text/csv',b'supp')]
    def expander(self,*a,**k):return nullcontext()
    def caption(self,*a,**k):pass
    def file_uploader(self,*a,**k):return self.uploads[0] if 'ledger' in k['key'] else self.uploads[1]
    def button(self,label,**kwargs):
        self.buttons.append(label);return label==self.button_name and not kwargs.get('disabled',False)
    def checkbox(self,*a,**k):return True
    def table(self,*a,**k):pass
    def warning(self,*a,**k):pass
    def json(self,*a,**k):pass
    def success(self,*a,**k):pass
    def error(self,message):self.errors.append(message)
    def rerun(self):self.reruns+=1

def body():return dict(review_sha256='a'*64,calculation={k:0 for k in ('opening_cash_usd','candidate_trading_usd','candidate_financing_usd','candidate_net_cash_change_usd','ending_cash_usd')})
def setup():
    st=Screen('Review Forex cash sources');service=SimpleNamespace(review_forex_cash_sources=Mock(return_value=body()),
        save_forex_cash_sources=Mock(),forex_cash_source_preview=Mock(return_value=body()))
    draft=SimpleNamespace(year=2026,month=10,id='draft',revision=2)
    return st,service,draft

def draw(st,s,d):render(st,s,'owner','account',d,key='key')

@pytest.mark.parametrize('change',['file','revision','account'])
def test_changed_context_discards_review_before_save(change):
    st,s,d=setup();draw(st,s,d);assert st.session_state
    st.button_name='Save Forex source confirmation'
    if change=='file':st.uploads[0]=RestoredUpload('cash.csv','text/csv',b'new')
    elif change=='revision':d.revision+=1
    else:
        render(st,s,'owner','other',d,key='key')
        s.save_forex_cash_sources.assert_not_called();assert not st.session_state;return
    draw(st,s,d);s.save_forex_cash_sources.assert_not_called();assert not st.session_state

def test_save_uses_review_digest_and_explicit_choices_then_clears_state():
    st,s,d=setup();draw(st,s,d);st.button_name='Save Forex source confirmation';draw(st,s,d)
    assert s.save_forex_cash_sources.call_args.kwargs==dict(reviewed_preview_sha256='a'*64,confirm_same_account=True,confirm_source_choices=True,replace=True)
    assert not st.session_state and st.reruns==1

def test_failed_fresh_review_removes_prior_approval():
    st,s,d=setup();draw(st,s,d);s.review_forex_cash_sources.side_effect=ValueError('stale source')
    draw(st,s,d);assert not st.session_state and st.errors==['stale source']
