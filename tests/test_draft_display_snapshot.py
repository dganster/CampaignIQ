from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch,Mock
import pytest
from campaigniq.account_deployment_application import DeploymentMonthlyApplication

@pytest.fixture
def display():
    actor=object();events=[];sessions=[];draft=SimpleNamespace(id='draft',year=2026,month=11,revision=3)
    @contextmanager
    def transaction(user,account):
        if user is not actor or account!='account':raise PermissionError('Foreign scope')
        session=SimpleNamespace(artifacts=SimpleNamespace(list_keys=lambda **kw:('saved',)),active=True)
        session._check=lambda:events.append('check')
        sessions.append(session);events.append('enter')
        try:yield session
        finally:session.active=False;events.append('exit')
    inputs=SimpleNamespace(gateway=SimpleNamespace(transaction=transaction),_lock=lambda s,i:draft)
    app=object.__new__(DeploymentMonthlyApplication);app._inputs=inputs
    return SimpleNamespace(app=app,actor=actor,draft=draft,events=events,sessions=sessions)

def patches(c,*,published=False,confirmed=False):
    from contextlib import ExitStack
    stack=ExitStack()
    stack.enter_context(patch('campaigniq.account_deployment_application._published_session',return_value=published))
    c.replay=stack.enter_context(patch('campaigniq.account_deployment_application._deployment_results_session'))
    c.metadata=stack.enter_context(patch('campaigniq.account_draft_recovery.metadata',side_effect=lambda s,d:[{'filename':'exact.pdf'}]))
    c.receipt=stack.enter_context(patch('campaigniq.account_no_trading.receipt',return_value={} if confirmed else None))
    return stack

@pytest.mark.parametrize('published,confirmed',[(False,False),(False,True),(True,False)])
def test_one_fresh_transaction_contains_display_reads_and_expires(display,published,confirmed):
    c=display
    with patches(c,published=published,confirmed=confirmed):
        result=c.app.draft_display(c.actor,'account','draft',3)
        assert result==dict(published=published,inventory=dict(revision=3,documents=[{'filename':'exact.pdf'}],protected=True),no_trading=confirmed)
        assert len(c.sessions)==1 and not c.sessions[0].active and c.events==['enter','check','exit']
        assert c.replay.call_count==int(published) and c.receipt.call_count==int(not published)
        result['inventory']['documents'].clear()
        assert len(c.app.draft_display(c.actor,'account','draft',3)['inventory']['documents'])==1
        assert len(c.sessions)==2 and not c.sessions[-1].active

@pytest.mark.parametrize('case',['actor','account','stale','bool'])
def test_foreign_or_stale_request_returns_no_display(display,case):
    c=display
    with patches(c):
        with pytest.raises((PermissionError,ValueError)):
            c.app.draft_display(object() if case=='actor' else c.actor,'foreign' if case=='account' else 'account','draft',2 if case=='stale' else (True if case=='bool' else 3))
        c.metadata.assert_not_called();c.replay.assert_not_called()
        assert all(not s.active for s in c.sessions)

def test_failed_published_replay_withholds_inventory_and_closes_session(display):
    c=display
    with patches(c,published=True):
        c.replay.side_effect=ValueError('Receipt conflict')
        with pytest.raises(ValueError):c.app.draft_display(c.actor,'account','draft',3)
        c.metadata.assert_not_called();assert not c.sessions[0].active

@pytest.mark.parametrize('published',[True,False])
def test_ui_consumes_display_snapshot_without_repeating_status_inventory_or_readiness(published):
    from streamlit.testing.v1 import AppTest
    from tests import test_monthly_review_cleanup as ui
    with patch.object(ui.AppTest,'from_string',side_effect=lambda source:SimpleNamespace(run=lambda **kw:source)):
        source=ui.screen(published=published,saved=True)
    extra='''
class Combined(Service):
    def draft_display(self,*args):
        st.session_state.calls.append('display')
        return dict(published=PUBLISHED,inventory=dict(protected=PUBLISHED,documents=[dict(id='doc',role='schwab_brokerage_statement',purpose='Brokerage statement',filename='Statement.pdf',superseded=False)]),no_trading=False)
    def published(self,*args):raise AssertionError('Repeated publication read')
    def saved_documents(self,*args):raise AssertionError('Repeated inventory read')
    def no_trading_status(self,*args):raise AssertionError('Repeated status read')
combined=Combined()
def no_readiness(*a):raise AssertionError('Duplicate readiness read')
combined.gateway.storage_ready=no_readiness
render(st,combined,USER,deployment=True)
'''.replace('PUBLISHED',str(published))
    source=source.replace('render(st,Service(),USER,deployment=True)',extra)
    at=AppTest.from_string(source).run(timeout=15)
    assert not at.exception and at.session_state.calls==['display']
    assert any(x.value=='Saved documents' for x in at.subheader)
    assert ('View published results' in [b.label for b in at.button]) is published
