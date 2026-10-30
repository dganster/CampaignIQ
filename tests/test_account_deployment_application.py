from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock,patch
import pytest
from campaigniq import account_deployment_application as a
from campaigniq.account_backend_config import AccountDatabaseTarget
from campaigniq.persistence.deployment_account_directory import PostgresAccountDirectory
from tests.test_account_deployment_publication import make_stores


def stores():
    reader,writer=make_stores();target=replace(reader.target,user='account_preferences')
    preferences=PostgresAccountDirectory('host=db.example.invalid port=5432 dbname=accounts user=account_preferences sslmode=verify-full',reader.settings,target)
    return reader,writer,preferences


def test_construction_has_no_connections_and_roles_remain_separate():
    reader,writer,preferences=stores()
    with patch('psycopg.connect',side_effect=AssertionError('No construction connection')):
        app=a.DeploymentApplication(reader,writer,preferences)
    assert not app.inputs.publication_enabled
    assert app.preferences.store is preferences
    assert app.inputs._publisher.writer is writer
    assert app.inputs._inputs.gateway._directory is reader
    with pytest.raises(PermissionError):app.inputs.publish(object(),'account','draft',0,confirm_publication=True)
    with pytest.raises(PermissionError):app.inputs.gateway.initialize(object())

@pytest.mark.parametrize('change',['same-role','same-preference-role','host','database','port','registry','issuer','mapping','local-store','flag'])
def test_mixed_targets_identity_maps_or_shared_roles_rejected_before_connection(change):
    reader,writer,preferences=stores();enabled=False
    if change=='same-role':writer=reader
    elif change=='same-preference-role':preferences=writer
    elif change=='local-store':reader=object()
    elif change=='flag':enabled=1
    elif change in ('host','database','port'):
        preferences.target=replace(preferences.target,**{change:{'host':'other.invalid','database':'other','port':5433}[change]})
    elif change=='registry':preferences.settings=replace(preferences.settings,registry_id='00000000-0000-0000-0000-000000000099')
    elif change=='issuer':preferences.settings=replace(preferences.settings,trusted_issuer='https://other.invalid')
    elif change=='mapping':preferences.settings=replace(preferences.settings,legacy_authorization=(('other','workspace-other'),))
    with patch('psycopg.connect',side_effect=AssertionError('No failed-construction connection')),pytest.raises(ValueError):
        a.DeploymentApplication(reader,writer,preferences,publication_enabled=enabled)


def test_methods_preserve_server_user_and_exact_source_revision_and_market_confirmation():
    app=a.DeploymentApplication(*stores(),publication_enabled=True);user=object();backend=app.inputs._inputs
    for method in ('drafts','create','save','preview','reconcile'):
        with patch.object(backend,method,return_value='result') as call:
            assert getattr(app.inputs,method)(user,'account','source',revision=4)=='result'
            call.assert_called_once_with(user,'account','source',revision=4)
    with patch.object(app.inputs._publisher,'publish',return_value='generation') as call:
        assert app.inputs.publish(user,'account','draft',4,confirm_publication=True,forex_applicable=False,crypto_applicable=False)=='generation'
        call.assert_called_once_with(user,'account','draft',4,confirm_publication=True,forex_applicable=False,crypto_applicable=False)
    with patch.object(a,'load_deployment_results',return_value='result') as call:
        assert app.inputs.results(user,'account','draft',4)=='result'
        call.assert_called_once_with(backend,user,'account','draft',4)
    with patch.object(a,'compare_deployment_accounts',return_value='comparison') as call:
        assert app.inputs.compare(user,2025,1,(),include_positions=False)=='comparison'
        call.assert_called_once_with(backend,user,2025,1,(),include_positions=False)


def test_published_status_rejects_stale_revision_and_verifies_current_canonical_sources():
    app=a.DeploymentApplication(*stores());backend=app.inputs._inputs
    session=SimpleNamespace(_check=Mock())
    @contextmanager
    def transaction(*args):yield session
    with patch.object(backend.gateway,'transaction',new=transaction),patch.object(backend,'_lock',return_value=SimpleNamespace(revision=4)),patch.object(a,'_published_session',return_value=True) as visible,patch.object(a,'_deployment_results_session') as results:
        with pytest.raises(ValueError,match='changed'):app.inputs.published(object(),'account','draft',3)
        assert not visible.called and not results.called
        assert app.inputs.published(object(),'account','draft',4)
        assert visible.call_args.kwargs['receipt_format']=='campaigniq.deployment_account_publication'
        assert results.call_count==1 and session._check.call_count==1
        results.side_effect=ValueError('Current source conflict')
        with pytest.raises(ValueError,match='conflict'):app.inputs.published(object(),'account','draft',4)


def test_renderer_requires_explicit_application_and_does_not_select_runtime():
    from campaigniq.ui import account_dashboard_view
    app=a.DeploymentApplication(*stores());ui=object()
    with patch.object(account_dashboard_view,'render') as render:
        a.render_application(ui,app)
        render.assert_called_once_with(ui,app,administration=app.administration,deployment=True)
    with pytest.raises(ValueError):a.render_application(ui,object())
