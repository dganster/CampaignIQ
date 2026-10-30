from pathlib import Path
from streamlit.testing.v1 import AppTest
from tests.test_account_dashboard_view import app_code


def inputs_screen(*,enabled=True,ready=True):
    # Reuse the existing realistic widget harness, selecting the deployment route.
    text=Path(__file__).with_name('test_account_signedin_inputs_view.py').read_text()
    code=text.split("    code = '''",1)[1].split("'''",1)[0]
    for key,value in (('READY',ready),('WRITABLE',True),('ADMIN',True),('DRAFT',True),('ACCOUNTS',True),('MISMATCH',False),('PUBLISHED',False)):
        code=code.replace(key,str(value))
    code=code.replace('class Service:\n','class Service:\n    publication_enabled='+str(enabled)+'\n')
    code=code.replace("kwargs['authorize_local_publication']","kwargs['confirm_publication']")
    code=code.replace('render(st,Service(),USER)','render(st,Service(),USER,deployment=True)')
    return AppTest.from_string(code).run(timeout=20)


def test_deployment_month_confirmation_uses_specific_account_period_and_publication_kwarg():
    app=inputs_screen()
    assert not app.exception
    assert not any('local' in b.label.lower() for b in app.button)
    confirm=next(c for c in app.checkbox if c.label.startswith('I authorize publication for'))
    assert 'Second brokerage' in confirm.label and '2025-01' in confirm.label
    next(b for b in app.button if b.label=='Publish month').click().run(timeout=20)
    assert app.error and app.session_state['calls']==[]
    next(c for c in app.checkbox if c.label.startswith('I authorize publication for')).check()
    next(b for b in app.button if b.label=='Publish month').click().run(timeout=20)
    assert not app.exception and app.session_state['calls']==['publish']
    assert any('This month is published.' in x.value for x in app.success)


def test_disabled_publication_and_unverified_storage_have_no_publish_or_initialize_controls():
    app=inputs_screen(enabled=False)
    assert not app.exception and not any(b.label in ('Publish month','Publish locally') for b in app.button)
    assert any('not enabled' in c.value for c in app.caption)
    app=inputs_screen(ready=False)
    assert not app.exception and not app.button and app.error
    assert app.session_state['calls']==[]


def test_shared_dashboard_deployment_route_keeps_account_view_state_separate():
    code=app_code().replace('render(UI(),Service(),administration=None)','render(UI(),Service(),administration=None,deployment=True)')
    app=AppTest.from_string(code).run(timeout=25)
    assert not app.exception
    assert any('Account workspace' in c.value for c in app.caption)
    next(r for r in app.radio if r.label=='Navigate').set_value('Positions').run(timeout=25)
    next(c for c in app.checkbox if c.label=='Fit columns to contents').check().run(timeout=25)
    next(s for s in app.selectbox if s.label=='Brokerage account').select('b').run(timeout=25)
    assert not app.exception and not next(c for c in app.checkbox if c.label=='Fit columns to contents').value
    next(s for s in app.selectbox if s.label=='Brokerage account').select('a').run(timeout=25)
    assert not app.exception and next(c for c in app.checkbox if c.label=='Fit columns to contents').value
