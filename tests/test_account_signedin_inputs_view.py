from streamlit.testing.v1 import AppTest


def screen(*, ready=True, writable=True, administrator=True, draft=True, accounts=True, mismatch=False, published=False):
    code = '''
import streamlit as st
from types import SimpleNamespace
from uuid import UUID
from campaigniq.account_administration import Identity
from campaigniq.account_legacy_access_rehearsal import RehearsalSession
from campaigniq.account_import_preview import AccountInputPreview, InputStatus
from campaigniq.account_reconciliation_preview import AccountReconciliationPreview, QuantityDifference
from campaigniq.persistence.import_drafts import Draft
from campaigniq.persistence.signedin_account_data import InputAccount, InputAccounts
from campaigniq.ui.account_signedin_inputs_view import render
USER = RehearsalSession(Identity('https://accounts.example.test','a'))
if 'ready' not in st.session_state:
    st.session_state.ready = READY
    st.session_state.has_draft = DRAFT
    st.session_state.revision = 0
    st.session_state.bound = False
    st.session_state.calls = []
class Gateway:
    _directory = SimpleNamespace(registry_id='registry-id')
    def overview(self,user):
        assert user is USER
        items = (InputAccount('account-id','Second brokerage',not WRITABLE,WRITABLE,
            (('securities','5678'),) if st.session_state.bound else ()),) if ACCOUNTS else ()
        return InputAccounts(ADMIN,st.session_state.revision,items,'actor-id')
    def storage_ready(self,user): return st.session_state.ready
    def initialize(self,user):
        assert user is USER
        st.session_state.ready=True
        st.session_state.calls.append('initialize')
    def bind(self,user,account,kind,number,*,expected_revision,confirm_replace):
        assert user is USER
        if expected_revision != st.session_state.revision: raise ValueError('Directory changed; reload before binding')
        st.session_state.bound=True
        st.session_state.revision+=1
        st.session_state.calls.append('bind')
class Service:
    gateway=Gateway()
    def drafts(self,user,account):
        assert user is USER
        return (Draft(UUID(int=1),'signedin-test',2025,1,0),) if st.session_state.has_draft else ()
    def create(self,user,account,year,month):
        assert user is USER
        st.session_state.has_draft=True
        st.session_state.calls.append('create')
    def save(self,*args,**kwargs): raise AssertionError('No file was selected')
    def published(self,*args): return st.session_state.get('published', PUBLISHED)
    def publish(self,user,account,identifier,revision,**kwargs):
        assert user is USER
        if kwargs['authorize_local_publication'] is not True: raise PermissionError('Confirmation required')
        st.session_state.calls.append('publish')
        st.session_state.published=True
    def results(self,user,account,identifier,revision):
        assert user is USER
        st.session_state.calls.append('results')
        return SimpleNamespace(month='2025-01',revision=4,original_documents=4,
            positions=(('SPY','110'),),lots=(('SPY','110','lot-a','Unknown','Unknown','Unassigned'),),
            brokerage_realized_usd='0',realized=(),forex_applicable=False,crypto_applicable=False)
    def preview(self,user,account,identifier,revision,**kwargs):
        assert user is USER
        st.session_state.calls.append('preview')
        return AccountInputPreview(account,str(identifier),revision,2025,1,False,
            (InputStatus('opening_state',False,'Missing opening evidence'),))
    def reconcile(self,user,account,identifier,revision,**kwargs):
        assert user is USER
        st.session_state.calls.append('closing-preview')
        differences=(QuantityDifference('SPY','110','111','-1'),) if MISMATCH else ()
        return AccountReconciliationPreview(account,str(identifier),revision,not MISMATCH,
            differences,(),(('SPY','110'),))
render(st,Service(),USER)
'''
    for key,value in (('READY',ready),('WRITABLE',writable),('ADMIN',administrator),('DRAFT',draft),('ACCOUNTS',accounts),('MISMATCH',mismatch),('PUBLISHED',published)):
        code = code.replace(key,str(value))
    return AppTest.from_string(code).run(timeout=15)


def button(at,label): return next(b for b in at.button if b.label == label)


def test_storage_requires_explicit_admin_initialization():
    at=screen(ready=False)
    assert not at.exception
    assert [b.label for b in at.button] == ['Enable local monthly input storage']
    button(at,'Enable local monthly input storage').click().run(timeout=15)
    assert not at.exception and at.session_state['calls'] == ['initialize']
    assert any(b.label == 'Create or resume draft' for b in at.button)


def test_member_cannot_initialize_storage():
    at=screen(ready=False,administrator=False)
    assert not at.exception and not at.button


def test_no_new_account_has_no_upload_or_initialization_controls():
    at=screen(accounts=False)
    assert not at.exception and not at.button and not at.selectbox


def test_binding_shows_only_suffix_after_save():
    at=screen()
    next(t for t in at.text_input if t.label == 'Full broker account identifier').set_value('12345678')
    button(at,'Save broker binding').click().run(timeout=15)
    assert not at.exception and at.session_state['calls'] == ['bind']
    assert any('5678' in success.value for success in at.success)
    assert not any('12345678' in success.value for success in at.success)


def test_stale_binding_form_requires_reload():
    at=screen()
    at.session_state['revision']=1
    next(t for t in at.text_input if t.label == 'Full broker account identifier').set_value('12345678')
    button(at,'Save broker binding').click().run(timeout=15)
    assert not at.exception and at.error
    assert not at.session_state['calls']


def test_create_resumes_selected_account_without_publication_controls():
    at=screen(draft=False)
    button(at,'Create or resume draft').click().run(timeout=15)
    assert not at.exception and at.session_state['calls']==['create']
    assert any(s.label == 'Saved month' for s in at.selectbox)
    assert any(b.label=='Publish locally' for b in at.button)


def test_view_only_account_has_preview_without_save_controls():
    at=screen(writable=False,administrator=False)
    assert not at.exception
    assert [b.label for b in at.button] == ['Preview saved inputs', 'Preview closing inventory']
    button(at,'Preview saved inputs').click().run(timeout=15)
    assert not at.exception and at.session_state['calls']==['preview']
    assert any('No data was published' in info.value for info in at.info)


def test_empty_upload_does_not_call_save():
    at=screen()
    button(at,'Save document').click().run(timeout=15)
    assert not at.exception and at.error and not at.session_state['calls']


def test_closing_preview_displays_quantity_without_publication_control():
    at=screen()
    button(at,'Preview closing inventory').click().run(timeout=15)
    assert not at.exception and at.session_state['calls']==['closing-preview']
    assert any('Closing inventory reconciles' in s.value for s in at.success)
    assert at.dataframe[0].value.iloc[0]['Quantity']=='110'
    assert 'publish' not in at.session_state['calls']


def test_closing_mismatch_remains_visible_and_does_not_show_success():
    at=screen(mismatch=True)
    button(at,'Preview closing inventory').click().run(timeout=15)
    assert not at.exception and at.warning
    assert not any('Closing inventory reconciles' in s.value for s in at.success)
    assert at.dataframe[1].value.iloc[0]['Difference']=='-1'


def test_publication_requires_explicit_confirmation():
    at=screen()
    button(at,'Publish locally').click().run(timeout=15)
    assert not at.exception and at.error and 'publish' not in at.session_state['calls']


def test_confirmed_local_publication_hides_write_controls_after_commit():
    at=screen()
    next(c for c in at.checkbox if c.label=='I authorize publication to the local development database').check()
    button(at,'Publish locally').click().run(timeout=15)
    assert not at.exception and at.session_state['calls']==['publish']
    assert not any(b.label in ('Save document','Publish locally') for b in at.button)
    assert any('published locally' in s.value for s in at.success)


def test_previously_published_month_remains_readable_without_replace_button():
    at=screen(published=True)
    assert not at.exception
    assert not any(b.label in ('Save document','Publish locally') for b in at.button)
    assert any(b.label=='Preview closing inventory' for b in at.button)


def test_published_results_read_selected_month_and_show_110_shares():
    at=screen(published=True)
    button(at,'View published results').click().run(timeout=15)
    assert not at.exception and at.session_state['calls']==['results']
    assert any('Published results · 2025-01' in s.value for s in at.subheader)
    assert at.dataframe[0].value.iloc[0]['Quantity']=='110'
    assert not any(b.label in ('Save document','Publish locally') for b in at.button)
