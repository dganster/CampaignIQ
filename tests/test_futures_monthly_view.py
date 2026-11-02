from pathlib import Path
from streamlit.testing.v1 import AppTest


def screen(*,writable=True,published=False):
    text=Path(__file__).with_name('test_account_signedin_inputs_view.py').read_text()
    code=text.split("    code = '''",1)[1].split("'''",1)[0]
    for key,value in (('READY',True),('WRITABLE',writable),('ADMIN',True),('DRAFT',True),('ACCOUNTS',True),('MISMATCH',False),('PUBLISHED',published)):
        code=code.replace(key,str(value))
    code=code.replace('class Service:\n', '''class Service:
    def futures_preview(self,*args):return {}
    def confirm_empty_futures_closing(self,user,account,draft,revision,*,confirm_empty_closing):
        if confirm_empty_closing is not True:raise ValueError('Explicit ending confirmation required')
        st.session_state.calls.append('closing')
    def futures_monthly_preview(self,user,account,draft,revision,**kwargs):
        assert user is USER and account=='account-id' and revision==0
        assert kwargs==dict(forex_applicable=True,crypto_applicable=True)
        st.session_state.calls.append('monthly')
        return dict(validations=[dict(role='history',valid=True,message='Derived projection')],
            securities_inventory_reconciled=True,futures=dict(realized_net_usd='-816'),
            source_findings=['Crypto statement still required'],inventory_mismatches=[],
            publication_blocker='Futures monthly publication remains disabled.')
''')
    code=code.replace('render(st,Service(),USER)','render(st,Service(),USER,deployment=True)')
    return AppTest.from_string(code).run(timeout=20)


def test_closing_checkbox_is_explicit_and_cannot_publish():
    app=screen()
    check=next(c for c in app.checkbox if c.label.startswith('I confirm this account had no futures'))
    assert 'January 31, 2025' in check.label and check.value is False
    next(b for b in app.button if b.label=='Save empty futures ending confirmation').click().run(timeout=20)
    assert app.error and app.session_state['calls']==[]
    next(c for c in app.checkbox if c.label.startswith('I confirm this account had no futures')).check()
    next(b for b in app.button if b.label=='Save empty futures ending confirmation').click().run(timeout=20)
    assert app.session_state['calls']==['closing']


def test_monthly_check_shows_crypto_blocker_and_readonly_source_lineage():
    app=screen()
    next(b for b in app.button if b.label=='Check monthly sources including futures').click().run(timeout=20)
    assert not app.exception and app.session_state['calls']==['monthly']
    assert any('Crypto statement' in w.value for w in app.warning)
    assert any('publication remains disabled' in i.value for i in app.info)
    assert any('original Trade History preserved' in c.value for c in app.caption)


def test_published_or_readonly_accounts_cannot_confirm_ending_positions():
    for args in ({'published':True},{'writable':False}):
        app=screen(**args)
        assert not app.exception
        assert not any(b.label=='Save empty futures ending confirmation' for b in app.button)
