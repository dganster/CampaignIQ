from pathlib import Path
from streamlit.testing.v1 import AppTest


def screen(*,writable=True,published=False):
    text=Path(__file__).with_name('test_account_signedin_inputs_view.py').read_text()
    code=text.split("    code = '''",1)[1].split("'''",1)[0]
    for key,value in (('READY',True),('WRITABLE',writable),('ADMIN',True),('DRAFT',True),('ACCOUNTS',True),('MISMATCH',False),('PUBLISHED',published)):
        code=code.replace(key,str(value))
    code=code.replace('class Service:\n', '''class Service:
    def save_futures_statement(self,*args,**kwargs):raise AssertionError('No file was selected')
    def futures_preview(self,user,account,draft,revision):
        assert user is USER and account=='account-id' and revision==0
        st.session_state.calls.append('futures-preview')
        return dict(realized_net_usd='-816.00',option_realized_usd='200.00',futures_realized_usd='-1000.00',
            fees_cash_usd='-16.00',calculated_ending_futures={},calculated_ending_options={},
            blockers=['Ending positions are calculated, not independently verified.','Publication integration remains pending.'])
''')
    code=code.replace('render(st,Service(),USER)','render(st,Service(),USER,deployment=True)')
    return AppTest.from_string(code).run(timeout=20)


def test_connected_preview_labels_evidence_and_never_calls_publish():
    app=screen()
    next(b for b in app.button if b.label=='Preview saved futures accounting').click().run(timeout=20)
    assert not app.exception and app.session_state['calls']==['futures-preview']
    assert any('not independently broker-verified' in c.value for c in app.caption)
    assert any('No month was published' in i.value for i in app.info)
    assert any('calculated' in w.value for w in app.warning)
    assert app.dataframe[0].value.iloc[2]['USD']=='-16.00'


def test_empty_pdf_selection_does_not_save_or_create_association():
    app=screen()
    next(b for b in app.button if b.label=='Save futures statement').click().run(timeout=20)
    assert not app.exception and app.error and app.session_state['calls']==[]
    assert next(c for c in app.checkbox if c.label.startswith('I confirm this futures statement')).value is False


def test_view_only_and_published_accounts_keep_preview_without_upload():
    for args in ({'writable':False},{'published':True}):
        app=screen(**args)
        assert not app.exception
        assert not any(b.label=='Save futures statement' for b in app.button)
        assert any(b.label=='Preview saved futures accounting' for b in app.button)
