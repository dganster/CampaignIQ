from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from tests.test_draft_display_snapshot import display,patches


def listing(c,values):
    original=c.app._inputs.gateway.transaction
    @contextmanager
    def transaction(*a,**kw):
        with original(*a,**kw) as session:
            session.drafts=SimpleNamespace(list_drafts=lambda:tuple(values))
            yield session
    c.app._inputs.gateway.transaction=transaction
    c.locked=[]
    def lock(session,identifier):
        c.locked.append(identifier)
        return next(d for d in values if d.id==identifier)
    c.app._inputs._lock=lock

@pytest.mark.parametrize('preferred',[None,'other'])
def test_one_transaction_lists_and_checks_only_the_selected_account_draft(display,preferred):
    c=display;other=SimpleNamespace(id='other',year=2026,month=12,revision=5)
    listing(c,[c.draft,other])
    with patches(c):
        result=c.app.draft_screen(c.actor,'account',preferred_draft_id=preferred)
    assert result['selected']==(preferred or 'other')
    assert result['display']['inventory']['revision']==5
    assert c.locked==[preferred or 'other'] and len(c.sessions)==1
    assert not c.sessions[0].active and len(result['drafts'])==2

def test_empty_account_returns_no_display_and_closes_session(display):
    c=display;listing(c,[])
    with patches(c):
        assert c.app.draft_screen(c.actor,'account')==dict(drafts=(),selected=None,display=None,publication_statuses={})
        c.metadata.assert_not_called()
    assert not c.sessions[0].active and not c.locked

@pytest.mark.parametrize('case',['actor','account','foreign-draft','empty','type'])
def test_foreign_scope_and_invalid_selection_return_no_metadata(display,case):
    c=display;listing(c,[c.draft])
    with patches(c):
        with pytest.raises((PermissionError,FileNotFoundError,ValueError)):
            c.app.draft_screen(object() if case=='actor' else c.actor,'other-account' if case=='account' else 'account',
                preferred_draft_id='foreign' if case=='foreign-draft' else ('' if case=='empty' else (True if case=='type' else None)))
        c.metadata.assert_not_called()
    assert all(not s.active for s in c.sessions)

def test_mismatched_revision_withholds_screen_and_fresh_call_observes_new_revision(display):
    c=display;listing(c,[c.draft])
    with patches(c):
        c.app._inputs._lock=lambda *a:SimpleNamespace(id='draft',year=2026,month=11,revision=4)
        with pytest.raises(ValueError,match='listing changed'):c.app.draft_screen(c.actor,'account')
        c.metadata.assert_not_called()
        c.draft.revision=4
        result=c.app.draft_screen(c.actor,'account')
        assert result['display']['inventory']['revision']==4
    assert len(c.sessions)==2 and all(not s.active for s in c.sessions)

@pytest.mark.parametrize('published',[True,False])
def test_actual_saved_month_selector_uses_new_screen_on_each_selection(published):
    from streamlit.testing.v1 import AppTest
    from tests import test_monthly_review_cleanup as ui
    with patch.object(ui.AppTest,'from_string',side_effect=lambda source:SimpleNamespace(run=lambda **kw:source)):
        source=ui.screen(published=published,saved=True)
    extra='''
class Combined(Service):
    def draft_screen(self,user,account,*,preferred_draft_id=None):
        assert user is USER and account=='account-id'
        values=(Draft(UUID(int=1),'scope',2025,1,0),Draft(UUID(int=2),'scope',2025,2,0))
        selected=preferred_draft_id or str(values[0].id)
        assert selected in [str(d.id) for d in values]
        st.session_state.calls.append('screen:'+selected)
        return dict(drafts=values,selected=selected,publication_statuses={str(d.id):PUBLISHED for d in values},display=dict(published=PUBLISHED,inventory=dict(protected=PUBLISHED,documents=[dict(id='doc',role='schwab_brokerage_statement',purpose='Brokerage statement',filename='Statement.pdf',superseded=False)]),no_trading=False))
    def drafts(self,*a):raise AssertionError('Separate listing')
    def draft_display(self,*a):raise AssertionError('Separate display')
    def saved_documents(self,*a):raise AssertionError('Separate inventory')
    def published(self,*a):raise AssertionError('Separate publication state')
    def no_trading_status(self,*a):raise AssertionError('Separate confirmation')
combined=Combined()
def no_readiness(*a):raise AssertionError('Duplicate readiness')
combined.gateway.storage_ready=no_readiness
render(st,combined,USER,deployment=True)
'''.replace('PUBLISHED',str(published))
    source=source.replace('render(st,Service(),USER,deployment=True)',extra)
    at=AppTest.from_string(source).run(timeout=15)
    assert not at.exception
    identifier='00000000-0000-0000-0000-000000000002'
    next(x for x in at.selectbox if x.label=='Saved month').select(identifier).run(timeout=15)
    assert not at.exception and at.session_state.calls[-1]=='screen:'+identifier
    assert ('View published results' in [b.label for b in at.button]) is published

def test_stale_ui_selection_has_an_explicit_reload_without_mutation():
    from streamlit.testing.v1 import AppTest
    from tests import test_monthly_review_cleanup as ui
    with patch.object(ui.AppTest,'from_string',side_effect=lambda source:SimpleNamespace(run=lambda **kw:source)):
        source=ui.screen(published=True,saved=True)
    extra='''
from campaigniq.account_deployment_application import DraftSelectionUnavailable
class Combined(Service):
    def draft_screen(self,user,account,*,preferred_draft_id=None):
        if preferred_draft_id=='missing':raise DraftSelectionUnavailable('Unavailable selection')
        values=(Draft(UUID(int=1),'scope',2025,1,0),)
        return dict(drafts=values,selected=str(values[0].id),display=dict(published=True,inventory=dict(protected=True,documents=[]),no_trading=False))
combined=Combined()
render(st,combined,USER,deployment=True)
'''
    source=source.replace('render(st,Service(),USER,deployment=True)',extra)
    at=AppTest.from_string(source)
    at.session_state['signedin-input:registry-id:actor-id:account-id:draft']='missing'
    at.run(timeout=15)
    assert not at.exception and any('Unavailable selection' in x.value for x in at.error)
    next(b for b in at.button if b.label=='Reload saved months').click().run(timeout=15)
    assert not at.exception and not at.error
    assert any(x.label=='Saved month' for x in at.selectbox)
    assert not at.session_state.calls
