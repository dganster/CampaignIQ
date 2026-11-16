from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from tests.test_draft_display_snapshot import display,patches
from tests.test_data_draft_screen import listing


def page_fixture(c,*,legacy=False,reusable=True):
    listing(c,[c.draft]);original=c.app._inputs.gateway.transaction
    c.directory=SimpleNamespace(revision=7,events=(),accounts=[SimpleNamespace(id='account',name='A',archived=False,legacy=legacy)],
        authorize=lambda identity,account:SimpleNamespace(writable=True,storage_scope='scope'))
    c.app._inputs.gateway._directory=SimpleNamespace(settings=SimpleNamespace(trusted_issuer='issuer'))
    @contextmanager
    def transaction(*a,**kw):
        with original(*a,**kw) as s:
            s._read_snapshot_reusable=lambda:reusable
            s._known_directory_snapshot=c.directory;s.retained_rows=(('retained','exact'),)
            yield s
    c.app._inputs.gateway.transaction=transaction
    return patch('campaigniq.account_signin.signed_in_accounts',return_value=SimpleNamespace(identity='identity',administrator=False,user_id='user'))


@pytest.mark.parametrize('legacy',[False,True])
def test_overview_retained_listing_and_draft_share_one_closed_snapshot(display,legacy):
    c=display
    with page_fixture(c,legacy=legacy),patches(c),patch('campaigniq.broker_account_binding.bindings',return_value={}),patch('campaigniq.account_legacy_results.published_months',return_value=('2025-01',)) as months:
        result=c.app.data_page(c.actor,'account')
        assert result['overview'].directory_revision==7
        assert [a.id for a in result['overview'].accounts]==['account']
        assert result['selected']=='draft' and result['retained_months']==(('2025-01',) if legacy else ())
        if legacy:assert months.call_args.args[0].read_text('retained')=='exact'
        else:months.assert_not_called()
    assert len(c.sessions)==1 and not c.sessions[0].active


@pytest.mark.parametrize('case',['actor','account','draft','not-readonly'])
def test_foreign_or_unproven_page_cannot_return_data(display,case):
    c=display
    with page_fixture(c,reusable=case!='not-readonly'),patches(c),patch('campaigniq.broker_account_binding.bindings',return_value={}):
        with pytest.raises((PermissionError,FileNotFoundError,ValueError)):
            c.app.data_page(object() if case=='actor' else c.actor,'foreign' if case=='account' else 'account',preferred_draft_id='foreign' if case=='draft' else None)
    assert all(not s.active for s in c.sessions)


def test_inputs_accept_detached_page_without_overview_or_draft_rereads():
    from streamlit.testing.v1 import AppTest
    from tests import test_monthly_review_cleanup as ui
    with patch.object(ui.AppTest,'from_string',side_effect=lambda source:SimpleNamespace(run=lambda **kw:source)):
        source=ui.screen(published=True,saved=True)
    extra='''
service=Service()
overview=service.gateway.overview(USER)
draft=service.drafts(USER,'account-id')[0]
page=dict(overview=overview,drafts=(draft,),selected=str(draft.id),display=dict(published=True,inventory=dict(protected=True,documents=[]),no_trading=False))
def forbidden(*a,**kw):raise AssertionError('Repeated display read')
service.gateway.overview=forbidden
service.drafts=forbidden
service.draft_screen=forbidden
render(st,service,USER,deployment=True,selected_account_id='account-id',page_data=page)
'''
    source=source.replace('render(st,Service(),USER,deployment=True)',extra)
    at=AppTest.from_string(source).run(timeout=15)
    assert not at.exception
    assert 'View published results' in [b.label for b in at.button]


def test_dashboard_passes_one_page_to_both_views_without_legacy_listing(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from tests.test_account_dashboard_view import app_code
    from campaigniq.ui import account_legacy_results_view,account_signedin_inputs_view
    # Restore script-level module substitutions before subsequent AppTests.
    monkeypatch.setattr(account_legacy_results_view,'render',account_legacy_results_view.render)
    monkeypatch.setattr(account_signedin_inputs_view,'render',account_signedin_inputs_view.render)
    extra='''
from campaigniq.ui import account_legacy_results_view as legacy_view,account_signedin_inputs_view as inputs_view
page=dict(retained_months=('2025-01',))
st.session_state.setdefault('page_calls',[])
def data_page(user,account,*,preferred_draft_id=None):
    st.session_state.page_calls.append(account)
    return page
def existing(st,service,user,account,*,deployment,months):
    assert deployment and months is page['retained_months']
def inputs(st,service,user,*,selected_account_id,deployment,page_data):
    assert deployment and page_data is page
legacy_view.render=existing
inputs_view.render=inputs
Service.continue_existing=True
Service.inputs=SimpleNamespace(data_page=data_page)
Service.legacy=SimpleNamespace()
render(UI(),Service(),administration=None,deployment=True)
'''
    source=app_code().replace('render(UI(),Service(),administration=None)',extra)
    at=AppTest.from_string(source).run(timeout=25)
    next(r for r in at.radio if r.label=='Navigate').set_value('Data').run(timeout=25)
    assert not at.exception and at.session_state.page_calls==['a']
