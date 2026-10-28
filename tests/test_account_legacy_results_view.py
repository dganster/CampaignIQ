from streamlit.testing.v1 import AppTest


def screen(*,empty=False,deny=False):
    return AppTest.from_string('''
import streamlit as st
from types import SimpleNamespace
from campaigniq.ui.account_legacy_results_view import render
USER=object()
if 'calls' not in st.session_state:st.session_state.calls=[]
class Service:
    _store=SimpleNamespace(registry_id='registry-id')
    def published_months(self,user,account):
        assert user is USER and account=='existing-a'
        return () if EMPTY else ('2025-01',)
    def published_results(self,user,account,year,month):
        assert user is USER and account=='existing-a' and (year,month)==(2025,1)
        if DENY:raise PermissionError('private financial detail')
        st.session_state.calls.append('read')
        return SimpleNamespace(month='2025-01',source_artifacts=9,positions=(('SPY','110'),),
            lots=(('SPY','110','Unknown','Unknown','Unassigned'),),brokerage_realized_usd='12.34',realized=(),
            forex_settled_usd=None,crypto_status='No retained crypto artifact',crypto_positions=(),
            warnings=('Forex coverage unknown.',))
render(st,Service(),USER,'existing-a')
'''.replace('EMPTY',str(empty)).replace('DENY',str(deny))).run(timeout=15)


def test_existing_month_results_show_saved_values_and_local_data_source():
    at=screen()
    at.button[0].click().run(timeout=15)
    assert not at.exception and at.session_state['calls']==['read']
    assert at.dataframe[0].value.iloc[0]['Quantity']=='110'
    assert any('retained LOCAL workspace copy' in c.value for c in at.caption)
    assert any('12.34' in m.value for m in at.markdown)
    assert any('Forex coverage unknown' in w.value for w in at.warning)
    assert not any('publish' in b.label.lower() and b.label!='View existing published results' for b in at.button)


def test_no_saved_published_month_has_no_read_button():
    at=screen(empty=True)
    assert not at.exception and not at.button and not at.selectbox


def test_revoked_access_returns_no_financial_table_or_private_error():
    at=screen(deny=True)
    at.button[0].click().run(timeout=15)
    assert not at.exception and at.error and not at.dataframe
    assert 'private financial detail' not in at.error[0].value
