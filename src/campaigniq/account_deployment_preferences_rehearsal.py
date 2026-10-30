"""Restricted, independently committed preference checks in the disposable clone."""
from concurrent.futures import ThreadPoolExecutor,TimeoutError
from contextlib import contextmanager
from threading import Barrier,Event,Lock
from unittest.mock import patch
from campaigniq.account_deployment_preferences import DeploymentAccountPreferences,TABLE
from campaigniq.account_preferences import LocalAccountPreferences,PreferenceIntegrityError
from campaigniq.account_administration import Permission
from campaigniq.account_backend_rehearsal import ServerSession
from campaigniq.ui.preferences import DEFAULT_PREFERENCES


def verify_preferences(c,passed):
    import psycopg
    a,b=c.accounts;service=DeploymentAccountPreferences(c.store)
    values={**DEFAULT_PREFERENCES,'fit_columns':True,'timezone':'America/Denver'}
    initial=c.snapshot()
    def command(action,**values):return c.reader.execute(c.user,c.reader.snapshot(c.user).revision,action,**values)
    directory=c.reader.snapshot(c.user);other=directory.user(c.foreign.identity).id
    passed('separate_preference_grants_are_reviewed_idempotent_and_rollback_after_injected_failure')
    assert service.load(c.user,a).revision==0
    assert service.save(c.user,a,values,expected_revision=0)==1
    assert dict(DeploymentAccountPreferences(c.store).load(c.user,a).values)==values
    assert service.load(c.user,b).revision==0
    # Metadata administrator is not a financial shortcut into the other owner.
    foreign_account=next(x.id for x in directory.accounts if x.owner_id==other)
    try:service.load(c.user,foreign_account)
    except PermissionError:pass
    else:raise AssertionError('Administrator accessed another owner preferences')
    passed('independent_connections_persist_personal_choices_and_keep_account_and_owner_keys_separate')
    before=c.snapshot()
    for user,revision in ((c.user,0),(c.foreign,1),(c.user.identity,1)):
        try:service.save(user,a,values,expected_revision=revision)
        except (ValueError,PermissionError):pass
        else:raise AssertionError('Stale or forged preference request succeeded')
    assert c.snapshot()==before
    passed('stale_revision_foreign_actor_and_forged_signin_leave_every_row_unchanged')
    barrier=Barrier(2)
    def save_first():
        barrier.wait(timeout=10)
        try:return service.save(c.user,b,values,expected_revision=0)
        except ValueError:return 'stale'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(save_first) for _ in range(2)]
        assert sorted((x.result(timeout=15) for x in futures),key=str)==[1,'stale']
    assert service.load(c.user,b).revision==1
    passed('simultaneous_first_save_has_one_winner_without_lost_updates')
    command('set_grant',user_id=other,account_id=a,permission=Permission.VIEW)
    assert service.load(c.foreign,a).revision==0
    assert service.save(c.foreign,a,dict(DEFAULT_PREFERENCES),expected_revision=0)==1
    assert dict(service.load(c.user,a).values)==values
    command('configure_account',account_id=a,archived=True)
    assert service.save(c.foreign,a,values,expected_revision=1)==2
    command('configure_account',account_id=a,archived=False)
    passed('view_grantee_and_archived_history_have_separate_personal_choices_without_financial_write_access')
    original=LocalAccountPreferences._read;before=c.snapshot()
    def fail_after_write(conn,key,**kwargs):
        row=original(conn,key,**kwargs)
        if row[0]==3:raise RuntimeError('injected preference verification failure')
        return row
    with patch.object(LocalAccountPreferences,'_read',new=staticmethod(fail_after_write)):
        try:service.save(c.foreign,a,values,expected_revision=2)
        except RuntimeError:pass
        else:raise AssertionError('Injected verification failure missing')
    assert c.snapshot()==before
    passed('post_write_verification_failure_rolls_back_personal_row_and_revision')
    # Catch a SQL error inside the adapter: transaction remains failed and cannot commit.
    def caught_sql(conn,key,**kwargs):
        row=original(conn,key,**kwargs)
        if row[0]==3:
            try:conn.execute('SELECT 1/0')
            except psycopg.errors.DivisionByZero:pass
        return row
    with patch.object(LocalAccountPreferences,'_read',new=staticmethod(caught_sql)):
        try:service.save(c.foreign,a,values,expected_revision=2)
        except ValueError:pass
        else:raise AssertionError('Caught SQL failure committed preference')
    assert c.snapshot()==before
    passed('caught_sql_failure_cannot_commit_partial_preference_changes')
    started=Event();release=Event();revoking=Event();seen=Lock();paused=[False]
    def paused_read(conn,key,**kwargs):
        row=original(conn,key,**kwargs)
        with seen:
            first=not paused[0];paused[0]=True
        if first:
            started.set()
            if not release.wait(15):raise RuntimeError('Preference coordination timed out')
        return row
    def revoke():
        revoking.set();return command('set_grant',user_id=other,account_id=a,permission=None)
    with patch.object(LocalAccountPreferences,'_read',new=staticmethod(paused_read)),ThreadPoolExecutor(max_workers=2) as pool:
        saving=pool.submit(service.save,c.foreign,a,values,expected_revision=2)
        try:
            assert started.wait(10)
            revocation=pool.submit(revoke);assert revoking.wait(10)
            try:revocation.result(timeout=.2)
            except TimeoutError:pass
            else:raise AssertionError('Revocation bypassed active preference lock')
            release.set();assert saving.result(timeout=15)==3;revocation.result(timeout=15)
        finally:release.set()
    for call in (lambda:service.load(c.foreign,a),lambda:service.save(c.foreign,a,values,expected_revision=3)):
        try:call()
        except PermissionError:pass
        else:raise AssertionError('Revoked preferences remained accessible')
    command('configure_user',user_id=other,enabled=False)
    try:service.load(c.foreign,foreign_account)
    except PermissionError:pass
    else:raise AssertionError('Disabled user loaded preferences')
    command('configure_user',user_id=other,enabled=True)
    passed('revocation_waits_for_active_preference_save_and_later_requests_and_disabled_users_are_blocked')
    # Corrupt only a new disposable preference row; preserve it until clone removal.
    with c.owner() as conn:
        conn.execute('UPDATE '+TABLE+" SET document_sha256=%s WHERE registry_id=%s AND account_id=%s",('0'*64,c.store.registry_id,b))
    before=c.snapshot()
    for call in (lambda:service.load(c.user,b),lambda:service.save(c.user,b,values,expected_revision=1)):
        try:call()
        except PreferenceIntegrityError:pass
        else:raise AssertionError('Corrupt preferences accepted or overwritten')
    assert c.snapshot()==before
    passed('corrupt_preference_document_is_blocked_without_repair_or_overwrite')
    for role in c.roles:
        with c.restricted(role,readonly=False) as conn:
            if role==c.roles[2]:
                commands=('UPDATE campaigniq_accounts.artifact_storage_dev SET content=content WHERE false',
                    'UPDATE campaigniq_accounts.import_drafts SET revision=revision WHERE false',
                    'UPDATE campaigniq_accounts.import_draft_documents SET filename=filename WHERE false',
                    'UPDATE public.artifact_storage_dev SET content=content WHERE false',
                    'DELETE FROM '+TABLE+' WHERE false')
            else:commands=('UPDATE '+TABLE+' SET revision=revision WHERE false',)
            for query in commands:
                try:
                    with conn.transaction():conn.execute(query)
                except psycopg.errors.InsufficientPrivilege:pass
                else:raise AssertionError('Restricted role accepted forbidden write')
    with c.store._connection(readonly=True) as conn:
        try:
            with conn.transaction():conn.execute('UPDATE '+TABLE+' SET revision=revision WHERE false')
        except psycopg.errors.ReadOnlySqlTransaction:pass
        else:raise AssertionError('Preference load connection accepted write')
    passed('database_roles_separate_preferences_from_inputs_publication_and_financial_writes_and_loads_are_readonly')
    excluded={'account_preferences','directories','audit_events'}
    after=c.snapshot()
    assert after['catalog_sha256']==initial['catalog_sha256']
    assert {k:v for k,v in after['rows'].items() if k not in excluded}=={k:v for k,v in initial['rows'].items() if k not in excluded}
    passed('all_financial_artifacts_originals_drafts_and_copied_personal_preferences_remain_unchanged')
