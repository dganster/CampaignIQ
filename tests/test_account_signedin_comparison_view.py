from streamlit.testing.v1 import AppTest


def screen(*,published=True,deny=False):
    code='''
import streamlit as st
from types import SimpleNamespace
from uuid import UUID
from campaigniq.account_signedin_comparison import PublishedComparison
from campaigniq.persistence.signedin_account_data import InputAccount,InputAccounts
from campaigniq.persistence.import_drafts import Draft
from campaigniq.ui.account_signedin_comparison_view import render
USER=object()
A='00000000-0000-0000-0000-000000000001'
B='00000000-0000-0000-0000-000000000002'
if 'calls' not in st.session_state:st.session_state.calls=[]
if 'revision' not in st.session_state:st.session_state.revision=4
class Gateway:
    _directory=SimpleNamespace(registry_id='registry-id')
    def overview(self,user):
        assert user is USER
        return InputAccounts(False,0,(InputAccount(A,'First',False,True,()),InputAccount(B,'Second',False,True,())),'user-id')
    def storage_ready(self,user):return True
class Service:
    gateway=Gateway()
    def drafts(self,user,account):
        assert user is USER
        return (Draft(UUID(account),'scope',2025,1,st.session_state.revision),)
    def published(self,*args):return PUBLISHED
    def compare(self,user,year,month,sources,*,include_positions):
        assert user is USER
        st.session_state.calls.append((len(sources),include_positions))
        if DENY:raise PermissionError('private account details')
        if any(s.revision!=st.session_state.revision for s in sources):raise ValueError('stale')
        rows=tuple(SimpleNamespace(account_id=s.account_id,positions=(('SPY','110' if s.account_id==A else '220'),),
            brokerage_realized_usd='0',original_documents=4,forex_applicable=False,crypto_applicable=False) for s in sources)
        return PublishedComparison('2025-01',rows,(('SPY','330'),) if include_positions else (),include_positions)
render(st,Service(),USER)
'''.replace('PUBLISHED',str(published)).replace('DENY',str(deny))
    return AppTest.from_string(code).run(timeout=15)


def button(at,label):return next(b for b in at.button if b.label==label)


def select_two(at):
    at.multiselect[0].set_value(['00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002']).run(timeout=15)
    return at


def test_no_implicit_selection_or_database_comparison():
    at=screen()
    assert not at.exception and not at.session_state['calls'] and not at.button


def test_selected_results_are_separate_and_totals_default_off():
    at=select_two(screen())
    button(at,'View account comparison').click().run(timeout=15)
    assert not at.exception and at.session_state['calls']==[(2,False)]
    assert len(at.dataframe)==2
    assert at.dataframe[0].value.iloc[0]['Quantity']=='110'
    assert at.dataframe[1].value.iloc[0]['Quantity']=='220'


def test_opt_in_combined_quantities_show_330_without_money_grand_total():
    at=select_two(screen())
    at.checkbox[0].check()
    button(at,'View account comparison').click().run(timeout=15)
    assert not at.exception and at.session_state['calls']==[(2,True)]
    assert len(at.dataframe)==3 and at.dataframe[2].value.iloc[0]['Quantity']=='330'
    assert not any('publish' in b.label.lower() for b in at.button)


def test_inaccessible_selection_shows_no_partial_rows_or_private_error():
    at=select_two(screen(deny=True))
    button(at,'View account comparison').click().run(timeout=15)
    assert not at.exception and at.error and not at.dataframe
    assert 'private account details' not in at.error[0].value


def test_no_common_published_month_has_no_submit():
    at=select_two(screen(published=False))
    assert not at.exception and not at.button and not at.session_state['calls']


def test_displayed_source_revision_pinned_until_explicit_reload():
    at=select_two(screen())
    at.session_state['revision']=5
    button(at,'View account comparison').click().run(timeout=15)
    assert not at.exception and at.error and not at.dataframe
    button(at,'Reload comparison sources').click().run(timeout=15)
    button(at,'View account comparison').click().run(timeout=15)
    assert not at.exception and at.dataframe
