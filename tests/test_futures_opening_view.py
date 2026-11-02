from pathlib import Path
from streamlit.testing.v1 import AppTest


def screen(*,writable=True,published=False):
    text=Path(__file__).with_name('test_account_signedin_inputs_view.py').read_text()
    code=text.split("    code = '''",1)[1].split("'''",1)[0]
    for key,value in (('READY',True),('WRITABLE',writable),('ADMIN',True),('DRAFT',True),('ACCOUNTS',True),('MISMATCH',False),('PUBLISHED',published)):
        code=code.replace(key,str(value))
    code=code.replace("2025,1,0)","2026,10,st.session_state.get('draft_revision',0))")
    code=code.replace('class Service:\n', '''class Service:
    def confirm_empty_futures_opening(self,user,account,draft,revision,*,confirm_empty_opening):
        if confirm_empty_opening is not True:raise ValueError('Explicit confirmation required')
        st.session_state.calls.append('futures-opening')
        st.session_state.opening=True
        st.session_state.draft_revision=revision+1
    def futures_opening_status(self,*args):
        st.session_state.calls.append('opening-status')
        return dict(confirmed=st.session_state.get('opening',False),opening_as_of='2026-09-30',broker_verified=False)
''')
    code=code.replace('render(st,Service(),USER)','render(st,Service(),USER,deployment=True)')
    return AppTest.from_string(code).run(timeout=20)


def button(app,label):return next(b for b in app.button if b.label==label)


def test_explicit_owner_confirmation_is_separate_and_never_publishes():
    app=screen()
    assert not app.exception
    check=next(c for c in app.checkbox if c.label.startswith('I confirm this account had no futures'))
    assert 'September 30, 2026' in check.label and check.value is False
    button(app,'Save empty futures opening confirmation').click().run(timeout=20)
    assert app.error and app.session_state['calls']==[]
    next(c for c in app.checkbox if c.label.startswith('I confirm this account had no futures')).check()
    button(app,'Save empty futures opening confirmation').click().run(timeout=20)
    assert not app.exception and app.session_state['calls']==['futures-opening']
    button(app,'Check saved futures opening confirmation').click().run(timeout=20)
    assert not app.exception and app.session_state['calls']==['futures-opening','opening-status']
    assert any('not independently broker-verified' in item.value for item in app.success)


def test_missing_status_never_infers_chat_confirmation():
    app=screen()
    button(app,'Check saved futures opening confirmation').click().run(timeout=20)
    assert any('No saved futures opening confirmation' in item.value for item in app.info)
    assert not any('confirmed by account owner' in item.value for item in app.success)


def test_published_and_view_only_accounts_have_no_confirmation_save():
    for arguments in ({'published':True},{'writable':False}):
        app=screen(**arguments)
        assert not app.exception
        assert not any(b.label=='Save empty futures opening confirmation' for b in app.button)
        assert any(b.label=='Check saved futures opening confirmation' for b in app.button)
