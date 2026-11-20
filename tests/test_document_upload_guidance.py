from types import SimpleNamespace
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from campaigniq.ui.account_signedin_inputs_view import document_guidance
from tests import test_monthly_review_cleanup as ui


def upload_screen():
    with patch.object(ui.AppTest,'from_string',side_effect=lambda source:SimpleNamespace(run=lambda **kw:source)):
        source=ui.screen()
    source=source.replace("def save(self,*args,**kwargs): raise AssertionError('No file was selected')", """def save(self,user,account,identifier,revision,role,upload,**kwargs):
        assert user is USER and account=='account-id'
        assert upload.getvalue()==b'exact original' and role=='schwab_brokerage_statement'
        st.session_state.calls.append(('saved',role,kwargs['replace']))""")
    # AppTest does not expose file selection. Supply bytes at the widget boundary;
    # retain the real view, role selection, save action and service call arguments.
    source=source.replace('render(st,Service(),USER,deployment=True)', '''
def upload_widget(label,*,key,**kwargs):
    st.session_state.upload_key=key
    return SimpleNamespace(name='Statement.pdf',type='application/pdf',getvalue=lambda:b'exact original') if st.session_state.get('file_ready',False) else None
st.file_uploader=upload_widget
render(st,Service(),USER,deployment=True)
''')
    return AppTest.from_string(source).run(timeout=20)


def test_file_presence_enables_save_without_implicit_write_and_removal_disables():
    app=upload_screen();app.radio[0].set_value('I traded').run()
    next(s for s in app.selectbox if s.label=='Document role').select('schwab_brokerage_statement').run()
    assert ui.button(app,'Save document').disabled and app.session_state.calls==[]
    app.session_state.file_ready=True;app.run()
    assert not app.exception and not ui.button(app,'Save document').disabled and app.session_state.calls==[]
    app.session_state.file_ready=False;app.run()
    assert ui.button(app,'Save document').disabled and app.session_state.calls==[]


def test_explicit_save_preserves_account_role_and_exact_bytes():
    app=upload_screen();app.radio[0].set_value('No trading activity').run()
    app.session_state.file_ready=True;app.run()
    ui.button(app,'Save document').click().run()
    assert not app.exception and not app.error
    assert app.session_state.calls==[('saved','schwab_brokerage_statement',False)]


def test_pending_upload_key_changes_when_role_changes():
    app=upload_screen();app.radio[0].set_value('I traded').run()
    next(s for s in app.selectbox if s.label=='Document role').select('schwab_brokerage_statement').run()
    first=app.session_state.upload_key
    next(s for s in app.selectbox if s.label=='Document role').select('thinkorswim_trade_history').run()
    assert app.session_state.upload_key!=first
    assert ui.button(app,'Save document').disabled and app.session_state.calls==[]


def test_conditional_sources_have_correct_boundary_months():
    opening=document_guidance('schwab_opening_position_snapshot',2026,1)
    assert 'December 2025' in opening and 'only when' in opening and 'January 2026' in opening
    assignment=document_guidance('schwab_next_month_assignment_evidence',2026,12)
    assert 'January 2027' in assignment and 'only when' in assignment and 'December 2026' in assignment


def test_guidance_keeps_trade_history_realized_results_and_statement_distinct():
    assert 'month-end positions' in document_guidance('schwab_brokerage_statement',2026,11)
    assert 'before reviewing a trading month' in document_guidance('thinkorswim_trade_history',2026,11)
    assert 'separate from Trade History' in document_guidance('schwab_realized_gain_loss',2026,11)
