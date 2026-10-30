"""Dashboard and comparison assertions inside the disposable committed clone job."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event
from unittest.mock import patch
import json
from campaigniq.account_dashboard import ReportSnapshot
from campaigniq.account_deployment_dashboard import DeploymentAccountDashboard
from campaigniq.account_deployment_comparison import compare_deployment_accounts
from campaigniq.account_signedin_comparison import PublishedSelection
from campaigniq.account_selection_rehearsal import synthetic_sources
from campaigniq.account_backend_rehearsal import ServerSession
from campaigniq.account_administration import Permission
from campaigniq.dashboard_sources import load_dashboard_sources
from campaigniq.account_legacy_results import published_months


def _assert_snapshot_bytes(snapshot,rows):
    actual=[(key,snapshot.read_text(key)) for key in snapshot.list_keys()]
    if len(actual)!=len(rows) or dict(actual)!=dict(rows):
        raise AssertionError("Retained dashboard artifact keys or exact content differ")


def verify_parity(c,passed):
    import psycopg
    a,b=c.accounts;dashboard=DeploymentAccountDashboard(c.reader)
    revision=c.revisions[b]
    for role,upload in synthetic_sources('87654321',double=True).items():
        revision=c.inputs.save(c.user,b,c.drafts[b].id,revision,role,upload,replace=True)
    c.revisions[b]=revision
    c.publisher.publish(c.user,b,c.drafts[b].id,revision,confirm_publication=True,forex_applicable=False,crypto_applicable=False)
    selections=tuple(PublishedSelection(account,c.drafts[account].id,c.revisions[account]) for account in (a,b))
    initial=c.snapshot();choices=dashboard.choices(c.user)
    assert {a,b}.issubset({item.id for item in choices.accounts})
    for account,period_count in ((a,2),(b,1)):
        snapshot=dashboard.snapshot(c.user,account,include_history=True)
        scope=c.reader.authorize(c.user,account).storage_scope
        with c.owner() as conn:rows=conn.execute('SELECT artifact_key,content FROM campaigniq_accounts.artifact_storage_dev WHERE namespace=%s ORDER BY artifact_key',(scope,)).fetchall()
        reference=ReportSnapshot(scope,rows)
        assert load_dashboard_sources(snapshot)==load_dashboard_sources(reference)
        assert snapshot.history and len(snapshot.history.originals)==period_count and not snapshot.history.gaps
        for key in snapshot.list_keys():assert snapshot.read_text(key)==dict(rows)[key]
        for method in ('write_text','delete','initialize'):
            try:getattr(snapshot,method)(*(() if method=='initialize' else ('forbidden','{}')))
            except PermissionError:pass
            else:raise AssertionError('Detached report snapshot accepted a write')
    assert c.snapshot()==initial
    passed('new_account_dashboard_bytes_existing_analytics_and_verified_history_match_independent_sources_without_writes')
    current=c.reader.snapshot(c.user);retained_count=0
    for identity in c.identities:
        actor=ServerSession(identity);owner_id=current.user(identity).id
        for account in (item for item in current.accounts if item.legacy and item.owner_id==owner_id):
            snapshot=dashboard.snapshot(actor,account.id,include_history=True)
            scope=c.reader.authorize(actor,account.id).storage_scope
            with c.owner() as conn:rows=conn.execute('SELECT artifact_key,content FROM public.artifact_storage_dev WHERE namespace=%s ORDER BY artifact_key',(scope,)).fetchall()
            _assert_snapshot_bytes(snapshot,rows)
            if any(k.endswith('-realized-attributions.json') for k,_ in rows):assert load_dashboard_sources(snapshot)==load_dashboard_sources(ReportSnapshot(scope,rows))
            assert snapshot.history is not None
            assert set(snapshot.history.published_months)=={x.month for x in snapshot.history.originals}|{g[:7] for g in snapshot.history.gaps}
            retained_count+=1
    assert retained_count>=2 and c.snapshot()==initial
    passed('every_retained_owner_dashboard_and_analytics_preserve_exact_namespace_sources_and_history_coverage')
    from campaigniq import account_deployment_comparison as module
    actual=c.inputs.gateway._connection;reads=[]
    from contextlib import contextmanager
    @contextmanager
    def observed(*,readonly):
        reads.append(readonly)
        with actual(readonly=readonly) as conn:yield conn
    with patch.object(c.inputs.gateway,'_connection',new=observed):
        separate=compare_deployment_accounts(c.inputs,c.user,2025,1,selections)
    assert reads.count(True)==1 and reads.count(False)==1
    assert [Decimal(item.positions[0][1]) for item in separate.accounts]==[110,220]
    assert separate.combined_positions==() and not separate.includes_combined_positions
    combined=compare_deployment_accounts(c.inputs,c.user,2025,1,selections,include_positions=True)
    assert Decimal(combined.combined_positions[0][1])==330 and c.snapshot()==initial
    passed('one_readonly_comparison_snapshot_keeps_110_and_220_separate_and_330_requires_opt_in')
    from dataclasses import replace
    for selection in ((selections[0],)*2,(selections[0],replace(selections[1],revision=0)),(selections[0],replace(selections[1],draft_id=selections[0].draft_id))):
        try:compare_deployment_accounts(c.inputs,c.user,2025,1,selection,include_positions=True)
        except (ValueError,FileNotFoundError):pass
        else:raise AssertionError('Invalid selection returned a comparison')
    try:compare_deployment_accounts(c.inputs,c.foreign,2025,1,selections)
    except PermissionError:pass
    else:raise AssertionError('Foreign owner returned a comparison')
    assert c.snapshot()==initial
    passed('duplicate_stale_foreign_and_unauthorized_selections_withhold_every_result_and_total_without_writes')
    scope=c.reader.authorize(c.user,b).storage_scope;key='2025-01-account-publication-receipt.json'
    with c.owner() as conn:receipt=conn.execute('SELECT content FROM campaigniq_accounts.artifact_storage_dev WHERE namespace=%s AND artifact_key=%s',(scope,key)).fetchone()[0]
    changed=json.loads(receipt);changed['draft_revision']=999
    started=Event();release=Event();original=module._deployment_results_session
    def pause(service,session,draft,account_id,revision):
        result=original(service,session,draft,account_id,revision)
        if account_id==a:
            started.set()
            if not release.wait(15):raise RuntimeError('Comparison coordination timed out')
        return result
    try:
        with patch.object(module,'_deployment_results_session',new=pause),ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(compare_deployment_accounts,c.inputs,c.user,2025,1,selections,include_positions=True)
            try:
                if not started.wait(10):raise RuntimeError('Comparison did not reach first account')
                with c.owner() as conn:conn.execute('UPDATE campaigniq_accounts.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',(json.dumps(changed),scope,key))
                release.set();coherent=future.result(timeout=15)
                assert coherent==combined
            finally:release.set()
        for call in (lambda:compare_deployment_accounts(c.inputs,c.user,2025,1,selections,include_positions=True),lambda:dashboard.snapshot(c.user,b)):
            try:call()
            except ValueError:pass
            else:raise AssertionError('Conflicting receipt returned dashboard or comparison')
    finally:
        with c.owner() as conn:conn.execute('UPDATE campaigniq_accounts.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',(receipt,scope,key))
    assert c.snapshot()==initial
    passed('concurrent_committed_change_cannot_mix_comparison_sources_and_conflicts_withhold_entire_dashboard_or_total')
    foreign_id=c.reader.snapshot(c.user).user(c.foreign.identity).id
    def command(action,**values):return c.reader.execute(c.user,c.reader.snapshot(c.user).revision,action,**values)
    for account in (a,b):command('set_grant',user_id=foreign_id,account_id=account,permission=Permission.VIEW)
    assert compare_deployment_accounts(c.inputs,c.foreign,2025,1,selections,include_positions=True)==combined
    command('set_grant',user_id=foreign_id,account_id=b,permission=None)
    try:compare_deployment_accounts(c.inputs,c.foreign,2025,1,selections,include_positions=True)
    except PermissionError:pass
    else:raise AssertionError('Partially revoked selection returned results')
    assert dashboard.snapshot(c.foreign,a).list_keys()
    command('set_grant',user_id=foreign_id,account_id=a,permission=None)
    try:dashboard.snapshot(c.foreign,a)
    except PermissionError:pass
    else:raise AssertionError('Revoked dashboard access persisted')
    command('configure_account',account_id=b,archived=True)
    assert next(item for item in dashboard.choices(c.user).accounts if item.id==b).archived
    assert dashboard.snapshot(c.user,b).list_keys()
    assert compare_deployment_accounts(c.inputs,c.user,2025,1,selections,include_positions=True)==combined
    command('configure_account',account_id=b,archived=False)
    command('configure_user',user_id=foreign_id,enabled=False)
    try:dashboard.choices(c.foreign)
    except PermissionError:pass
    else:raise AssertionError('Disabled actor retained dashboard access')
    command('configure_user',user_id=foreign_id,enabled=True)
    passed('whole_selection_revocation_archived_readonly_history_and_disabled_actor_are_rechecked')
