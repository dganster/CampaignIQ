from streamlit.testing.v1 import AppTest


def screen(protected=False):
    code='''
import streamlit as st
from types import SimpleNamespace
from campaigniq.ui.account_draft_recovery_view import render
if 'revision' not in st.session_state:
 st.session_state.revision=1
 st.session_state.removed=False
class Service:
 def saved_documents(self,user,account,draft,revision):
  assert (user,account,draft)==('signed-in','account-a','draft-a')
  return dict(protected=PROTECTED,documents=[dict(id='source-a',role='thinkorswim_trade_history',filename='October.csv',purpose='Trade History',superseded=st.session_state.removed)])
 def draft_recovery_preview(self,user,account,draft,revision,**opts):
  return dict(review=dict(action=opts['action'],document_id=opts['document_id'],affected_documents=[dict(purpose='Trade History',filename='October.csv')]),review_sha256='a'*64)
 def recover_draft(self,user,account,draft,revision,**opts):
  assert opts['confirm'] is True and opts['reviewed_sha256']=='a'*64
  st.session_state.removed=True
  st.session_state.revision+=2
stable='account-a:draft-a'
key=stable+':'+str(st.session_state.revision)
if st.session_state.get(stable+':notice'):st.success(st.session_state[stable+':notice'])
render(st,Service(),'signed-in','account-a',SimpleNamespace(id='draft-a',revision=st.session_state.revision),key=key,writable=True,published=False)
'''.replace('PROTECTED',str(protected))
    return AppTest.from_string(code).run(timeout=15)


def button(at,label):return next(b for b in at.button if b.label==label)

def test_remove_requires_review_and_explicit_confirmation_then_preserves_history_and_notice():
    at=screen();assert not at.exception
    at.selectbox[0].select('source-a').run()
    button(at,'Review removal').click().run()
    assert not at.exception and button(at,'Remove from this draft').disabled
    at.checkbox[0].check().run()
    button(at,'Remove from this draft').click().run()
    assert not at.exception and at.session_state['removed']
    assert any('Original uploads were preserved' in s.value for s in at.success)
    assert at.table[0].value.iloc[0]['Status']=='Superseded'
    at.run();assert any('Original uploads were preserved' in s.value for s in at.success)


def test_cancel_makes_no_change_and_protected_month_has_no_recovery_actions():
    at=screen();button(at,'Review Start this month over').click().run()
    button(at,'Cancel correction').click().run()
    assert not at.session_state['removed'] and not at.exception
    protected=screen(True)
    assert not protected.exception and not protected.button and not protected.selectbox


def test_guided_page_requires_activity_choice_and_hides_unrelated_market_controls():
    code='''
import streamlit as st
from types import SimpleNamespace
from uuid import UUID
from campaigniq.persistence.import_drafts import Draft
from campaigniq.ui.account_signedin_inputs_view import render
class Gateway:
 _directory=SimpleNamespace(registry_id='registry')
 _backend=SimpleNamespace(_continue_existing=True)
 def overview(self,user):return SimpleNamespace(accounts=[SimpleNamespace(id='account',name='Andrew',writable=True,archived=False,suffixes=(('securities','4285'),))],user_id='owner',administrator=False,directory_revision=1)
 def storage_ready(self,user):return True
class Service:
 gateway=Gateway()
 publication_enabled=True
 multi_asset_enabled=True
 def drafts(self,*args):return [Draft(UUID(int=1),'workspace',2026,10,1)]
 def published(self,*args):return False
 def no_trading_status(self,*args):return False
 def no_trading_preview(self,*args):raise AssertionError('Review is explicit')
 def saved_documents(self,*args):return dict(protected=False,documents=[])
 def futures_preview(self,*args):raise AssertionError('Unrelated futures hidden')
 def review_forex_cash_sources(self,*args):raise AssertionError('Unrelated Forex hidden')
render(st,Service(),'user',selected_account_id='account',deployment=True)
'''
    at=AppTest.from_string(code).run(timeout=15)
    assert not at.exception and not at.file_uploader
    at.radio[0].set_value('No trading activity').run()
    assert not at.exception
    assert not any('Forex' in c.label or 'Crypto' in c.label for c in at.checkbox)
    assert not any('Futures' in e.label or 'Forex' in e.label or 'Crypto' in e.label for e in at.expander)
    assert button(at,'Publish month').disabled
    assert not any(s.label=='Document role' for s in at.selectbox)
    assert any('Document: Brokerage statement' in c.value for c in at.caption)
    assert not any('replacement' in c.label.lower() for c in at.checkbox)
