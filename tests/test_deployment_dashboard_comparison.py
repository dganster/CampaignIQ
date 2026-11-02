from contextlib import contextmanager,ExitStack
from dataclasses import replace
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from campaigniq import account_deployment_comparison as comparison_module
from campaigniq import account_deployment_dashboard as dashboard_module
from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
from campaigniq.account_deployment_comparison import compare_deployment_accounts
from campaigniq.account_deployment_dashboard import DeploymentAccountDashboard
from campaigniq.account_reconciliation_preview import ReadOnlyArtifacts
from campaigniq.account_signin import signed_in_accounts
from campaigniq.dashboard_sources import load_dashboard_sources
from campaigniq.persistence.deployment_account_data import PostgresAccountDataGateway
from tests.test_account_deployment_publication import make_stores
from tests.test_account_signedin_comparison import comparison,results,signed,local,month,bound_context,context,FIXTURES

@pytest.fixture
def deployment(comparison,monkeypatch):
    c=comparison;reader,_=make_stores();reader.settings=replace(reader.settings,registry_id=c.registry)
    for account in (c.account,c.second):
        key='2025-01-account-publication-receipt.json';value=json.loads(c.artifacts[account].rows[key])
        value['format']='campaigniq.deployment_account_publication';c.artifacts[account].rows[key]=json.dumps(value)
    c.inputs=DeploymentMonthlyInputs(PostgresAccountDataGateway(reader));c.inputs._known=c.bound._known;c.inputs._lock=c.bound._lock
    c.dashboard=DeploymentAccountDashboard(reader);c.dashboard.inputs=c.inputs
    c.opened=[];c.source_snapshots=[]
    def actor(user):return signed_in_accounts(user,c.directory,trusted_issuer=c.admin.issuer)
    class Tx:
        def __init__(self,*args):pass
        def _actor(self,user):return c.directory,actor(user)
    @contextmanager
    def connection(*,readonly):
        assert readonly is True;c.source_snapshots.append(object())
        yield SimpleNamespace()
    @contextmanager
    def guard(user,account,**kwargs):c.directory.authorize(actor(user).identity,account);yield
    stack=ExitStack()
    def opened(conn,settings,user,account,**kwargs):
        session=stack.enter_context(c.bound.gateway.transaction(actor(user).identity,account))
        session._active=True;session._check=lambda:None
        session.artifacts=ReadOnlyArtifacts(c.artifacts[account]);c.opened.append(session)
        return session,0
    @contextmanager
    def transaction(user,account,**kwargs):
        session,_=opened(None,None,user,account)
        try:yield session
        finally:session._active=False
    reader._connection=connection
    monkeypatch.setattr(c.inputs.gateway,'_guard',guard);monkeypatch.setattr(c.inputs.gateway,'_connection',connection)
    monkeypatch.setattr(c.dashboard.gateway,'transaction',transaction)
    monkeypatch.setattr(comparison_module,'DirectoryTransaction',Tx);monkeypatch.setattr(comparison_module,'_open_session',opened)
    monkeypatch.setattr(dashboard_module,'DirectoryTransaction',Tx)
    yield c
    stack.close()


def test_one_snapshot_keeps_110_and_220_separate_and_330_requires_opt_in(deployment):
    c=deployment;before={k:dict(v.rows) for k,v in c.artifacts.items() if hasattr(v,'rows')}
    result=compare_deployment_accounts(c.inputs,c.user,2025,1,c.selections)
    assert len(c.source_snapshots)==1 and result.combined_positions==()
    assert [Decimal(x.positions[0][1]) for x in result.accounts]==[110,220]
    combined=compare_deployment_accounts(c.inputs,c.user,2025,1,c.selections,include_positions=True)
    assert Decimal(combined.combined_positions[0][1])==330
    assert all(not s._active for s in c.opened)
    assert before=={k:dict(v.rows) for k,v in c.artifacts.items() if hasattr(v,'rows')}


def test_stale_duplicate_foreign_month_and_invalid_options_return_no_comparison(deployment):
    c=deployment
    for values in ((c.selections[0],)*2,(c.selections[0],replace(c.selections[1],revision=0)),(c.selections[0],replace(c.selections[1],draft_id=c.draft.id))):
        with pytest.raises((ValueError,FileNotFoundError)):compare_deployment_accounts(c.inputs,c.user,2025,1,values,include_positions=True)
    with pytest.raises(ValueError):compare_deployment_accounts(c.inputs,c.user,2025,2,c.selections)
    with pytest.raises(ValueError):compare_deployment_accounts(c.inputs,c.user,2025,1,c.selections,include_positions=1)
    with pytest.raises(ValueError):compare_deployment_accounts(c.inputs,c.user,2025,1,())
    assert all(not s._active for s in c.opened)


def test_conflicting_receipt_withholds_comparison_and_dashboard_without_repair(deployment):
    c=deployment;key='2025-01-account-publication-receipt.json'
    bad=json.loads(c.artifacts[c.second].rows[key]);bad['draft_revision']=999
    c.artifacts[c.second].rows[key]=json.dumps(bad);before=dict(c.artifacts[c.second].rows)
    with pytest.raises(ValueError):compare_deployment_accounts(c.inputs,c.user,2025,1,c.selections,include_positions=True)
    with pytest.raises(ValueError):c.dashboard.snapshot(c.user,c.second)
    assert c.artifacts[c.second].rows==before


def test_dashboard_snapshot_feeds_existing_loader_and_rejects_mutation(deployment):
    c=deployment;choices=c.dashboard.choices(c.user)
    assert {a.id for a in choices.accounts}=={c.account,c.second}
    result=c.dashboard.snapshot(c.user,c.account)
    assert result.namespace==c.directory.authorize(c.admin,c.account).storage_scope
    expected={k:v for k,v in c.artifacts[c.account].rows.items() if k.startswith('2025-01-') or k=='.campaigniq-monthly-publication-v1.json'}
    assert {k:result.read_text(k) for k in result.list_keys()}==expected
    assert load_dashboard_sources(result)==load_dashboard_sources(c.artifacts[c.account])
    for method in ('write_text','delete','initialize'):
        with pytest.raises(PermissionError):getattr(result,method)(*(() if method=='initialize' else ('forbidden','{}')))
    with pytest.raises(TypeError):result._rows['forbidden']='{}'
    with pytest.raises(ValueError):c.dashboard.snapshot(c.user,c.account,include_history=1)


def test_revoked_access_is_rechecked_for_comparison_and_dashboard(deployment):
    from campaigniq.account_administration import Permission
    from campaigniq.account_legacy_access_rehearsal import RehearsalSession
    c=deployment;other=RehearsalSession(c.member)
    with pytest.raises(PermissionError):c.dashboard.snapshot(other,c.account)
    c.directory=c.directory.set_grant(c.admin,c.directory.user(c.member).id,c.account,Permission.VIEW,expected_revision=c.directory.revision)
    assert c.dashboard.snapshot(other,c.account).list_keys()
    c.directory=c.directory.set_grant(c.admin,c.directory.user(c.member).id,c.account,None,expected_revision=c.directory.revision)
    with pytest.raises(PermissionError):c.dashboard.snapshot(other,c.account)
    with pytest.raises(PermissionError):compare_deployment_accounts(c.inputs,other,2025,1,(c.selections[0],))


def test_retained_parity_ignores_collation_but_rejects_missing_extra_or_changed_bytes():
    from campaigniq.account_dashboard import ReportSnapshot
    from campaigniq.account_deployment_dashboard_rehearsal import _assert_snapshot_bytes
    rows=[('a-key','exact newline\n'),('A-key','{}')]
    snapshot=ReportSnapshot('scope',rows)
    _assert_snapshot_bytes(snapshot,rows)
    for wrong in (rows[:1],rows+[('extra','{}')],[(rows[0][0],'changed'),rows[1]],rows+[rows[0]]):
        with pytest.raises(AssertionError):_assert_snapshot_bytes(snapshot,wrong)
