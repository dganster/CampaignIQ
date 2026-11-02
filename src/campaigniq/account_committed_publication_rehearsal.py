"""Independent committed publication on a disposable clone of the fixed LOCAL restore.

Never commits data or grants in the retained restore. Never targets production.
Temporary clone removal requires its exact created OID/owner and unchanged copied
source rows. DROP DATABASE is never forced; role removal is dependency-restricted.
"""
from concurrent.futures import ThreadPoolExecutor,TimeoutError
from contextlib import contextmanager
from threading import Barrier,Event
from datetime import datetime,timezone
import os,pwd,re,json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from campaigniq import account_schema_migrations as m
from campaigniq.account_schema_rehearsal import restore_conninfo,inventory,DATABASE
from campaigniq.account_runtime_roles import review_runtime_grants,apply_runtime_grants,role_catalog_digest
from campaigniq.account_publication_roles import review_publication_grants,apply_publication_grants
from campaigniq.account_data_backend_rehearsal import save,schema_rows
from campaigniq.account_backend_rehearsal import ServerSession
from campaigniq.account_administration import Identity,LegacyOwner,Permission
from campaigniq.account_backend_config import AccountDirectorySettings,AccountDatabaseTarget
from campaigniq.account_directory_bootstrap import review_bootstrap,bootstrap_directory
from campaigniq.persistence.deployment_account_directory import PostgresAccountDirectory,DirectoryTransaction,validate_runtime_role,require_transaction
from campaigniq.persistence.deployment_account_data import PostgresAccountDataGateway
from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
from campaigniq.account_deployment_publication import DeploymentAccountPublisher,AppendOnlyArtifacts
from campaigniq.account_deployment_results import load_deployment_results
from campaigniq.account_selection_rehearsal import synthetic_sources
from campaigniq.account_publication_rehearsal import february_sources


def clone_name(value):
    if not isinstance(value,str) or not re.fullmatch(r'campaigniq_pub_rehearsal_[0-9a-f]{32}',value):
        raise ValueError('Exact generated disposable clone name required')
    return value


def cleanup_clone(control,name,oid,role_oids,before):
    from psycopg import sql
    clone_name(name)
    suffix=name.removeprefix('campaigniq_pub_rehearsal_')[:16]
    if not set(role_oids).issubset({'ciq_commit_input_'+suffix,'ciq_commit_publish_'+suffix,'ciq_commit_prefs_'+suffix}):
        raise ValueError('Temporary role identity names differ; retained for recovery')
    actual=control.execute('SELECT oid,pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s',(name,)).fetchone()
    if actual!=(oid,'postgres'):raise ValueError('Disposable database identity differs; retained for recovery')
    # No FORCE, CASCADE, DROP OWNED, role/password changes or retained-db cleanup.
    # This connection must be independent and closed before DROP DATABASE.
    import psycopg
    from psycopg.conninfo import make_conninfo
    with psycopg.connect(make_conninfo(dbname=name,user='postgres',host='/var/run/postgresql',port='5432',connect_timeout='5',options='-c timezone=UTC')) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        copied={k:v for k,v in inventory(conn).items() if not k.startswith(m.SCHEMA+'.')}
        if copied!=before:raise ValueError('Copied source tables differ; disposable database retained for recovery')
    for role,expected in role_oids.items():
        actual=control.execute('SELECT oid FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
        if actual!=(expected,):raise ValueError('Temporary role identity differs; retained for recovery')
    control.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
    with control.transaction():
        for role in role_oids:control.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def run_rehearsal(output,*,development_only,verify_dashboard=False,verify_preferences=False,verify_application=False,verify_futures=False):
    if any(type(x) is not bool for x in (verify_dashboard,verify_preferences,verify_application,verify_futures)) or sum((verify_dashboard,verify_preferences,verify_application,verify_futures))>1:raise ValueError('One explicit rehearsal mode required')
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    source_dsn=restore_conninfo(development_only=development_only)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    token=uuid4().hex;database=clone_name('campaigniq_pub_rehearsal_'+token)
    roles=['ciq_commit_input_'+token[:16],'ciq_commit_publish_'+token[:16]]
    if verify_preferences or verify_application:roles.append('ciq_commit_prefs_'+token[:16])
    role_oids={};roles_committed=False;clone_oid=None;before=None;before_acl=None
    report_name='futures-publication-report.json' if verify_futures else 'deployment-application-report.json' if verify_application else 'deployment-preferences-report.json' if verify_preferences else 'deployment-dashboard-report.json' if verify_dashboard else 'committed-publication-report.json'
    report=dict(format='campaigniq.futures-publication-rehearsal' if verify_futures else 'campaigniq.deployment-application-rehearsal' if verify_application else 'campaigniq.deployment-preferences-rehearsal' if verify_preferences else 'campaigniq.deployment-dashboard-rehearsal' if verify_dashboard else 'campaigniq.committed-publication-rehearsal',version=1,checks={},passed=False,
        source_database=DATABASE,disposable_database=database,production_connection_made=False,account_mode_activated=False)
    def passed(name):report['checks'][name]=True;print(name.replace('_',' ')+': PASS',flush=True)
    @contextmanager
    def owner():
        with psycopg.connect(make_conninfo(dbname=database,user='postgres',host='/var/run/postgresql',port='5432',connect_timeout='5',options='-c timezone=UTC')) as conn:
            conn.execute('SET LOCAL search_path=pg_catalog');conn.execute("SET LOCAL lock_timeout='5s'");conn.execute("SET LOCAL statement_timeout='30s'")
            if conn.execute('SELECT current_database(),current_user,inet_server_addr()').fetchone()!=(database,'postgres',None):raise ValueError('Unexpected disposable database target')
            yield conn
    def snapshot():
        with owner() as conn:return schema_rows(conn)
    control=None
    try:
        if pwd.getpwuid(os.getuid()).pw_name!='postgres':raise PermissionError('Use the existing local administrator launcher')
        with psycopg.connect(source_dsn) as source:
            source.execute('SET TRANSACTION READ ONLY')
            if source.execute('SELECT current_database(),current_user,inet_server_addr()').fetchone()!=(DATABASE,'postgres',None):raise ValueError('Unexpected retained restore target')
            if source.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(m.SCHEMA,)).fetchone():raise ValueError('Retained restore has deployment schema; stopped')
            before=inventory(source);before_acl=role_catalog_digest(source)
        # Close every source connection before CREATE DATABASE TEMPLATE.
        save(output/'prewrite-recovery-receipt.json',dict(source_database=DATABASE,original_table_sha256=before,
            original_catalog_sha256=before_acl,disposable_database=database,temporary_roles=roles,
            cleanup_policy='Exact created database OID and role OIDs; unchanged copied source rows; no FORCE or CASCADE'))
        control=psycopg.connect(make_conninfo(dbname='postgres',user='postgres',host='/var/run/postgresql',port='5432',connect_timeout='5',options='-c timezone=UTC'),autocommit=True)
        if control.execute('SELECT current_database(),current_user,inet_server_addr()').fetchone()!=('postgres','postgres',None):raise ValueError('Unexpected local control connection')
        control.execute("SET lock_timeout='5s'");control.execute("SET statement_timeout='30s'")
        if control.execute('SELECT 1 FROM pg_database WHERE datname=%s',(database,)).fetchone():raise ValueError('Clone already exists; no adoption')
        control.execute(sql.SQL('CREATE DATABASE {} WITH TEMPLATE {} OWNER postgres').format(sql.Identifier(database),sql.Identifier(DATABASE)))
        clone_oid=control.execute('SELECT oid FROM pg_database WHERE datname=%s',(database,)).fetchone()[0]
        control.execute(sql.SQL('REVOKE CONNECT ON DATABASE {} FROM PUBLIC').format(sql.Identifier(database)))
        with owner() as conn:
            assert inventory(conn)==before
            m.apply_migrations(conn)
            for role in roles:conn.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS').format(sql.Identifier(role)))
            for role in roles:role_oids[role]=conn.execute('SELECT oid FROM pg_roles WHERE rolname=%s',(role,)).fetchone()[0]
            plan=review_runtime_grants(conn,roles[0]);apply_runtime_grants(conn,roles[0],reviewed_sha256=m.digest(plan));save(output/'reviewed-grant-plan.json',plan)
            plan=review_publication_grants(conn,roles[1]);apply_publication_grants(conn,roles[1],reviewed_sha256=m.digest(plan));save(output/'reviewed-publication-grant-plan.json',plan)
            if verify_preferences or verify_application:
                from campaigniq.account_preference_roles import review_preference_grants,apply_preference_grants
                plan=review_preference_grants(conn,roles[2]);unchanged=role_catalog_digest(conn)
                try:
                    with conn.transaction():
                        apply_preference_grants(conn,roles[2],reviewed_sha256=m.digest(plan))
                        raise RuntimeError('injected preference grant failure')
                except RuntimeError:pass
                assert role_catalog_digest(conn)==unchanged
                plan=review_preference_grants(conn,roles[2])
                try:apply_preference_grants(conn,roles[2],reviewed_sha256='0'*64)
                except ValueError:pass
                else:raise AssertionError('Changed preference grant review accepted')
                assert apply_preference_grants(conn,roles[2],reviewed_sha256=m.digest(plan))
                repeat=review_preference_grants(conn,roles[2]);assert not apply_preference_grants(conn,roles[2],reviewed_sha256=m.digest(repeat))
                save(output/'reviewed-preference-grant-plan.json',plan)
            scopes=tuple(row[0] for row in conn.execute("SELECT DISTINCT namespace FROM public.artifact_storage_dev WHERE namespace LIKE 'workspace-%' ORDER BY 1").fetchall())
            if len(scopes)<2:raise ValueError('Two retained namespaces required')
            identities=tuple(Identity('https://committed-publication.invalid','synthetic-'+str(i)) for i in range(len(scopes)))
            settings=AccountDirectorySettings(str(uuid4()),identities[0].issuer,tuple(sorted((i.subject,w) for i,w in zip(identities,scopes))))
            owners=tuple(LegacyOwner(i,w,'Synthetic owner','Retained account','schwab') for i,w in zip(identities,scopes))
            plan=review_bootstrap(conn,settings,owners,identities[0]);bootstrap_directory(conn,settings,plan,reviewed_sha256=plan.sha256)
        roles_committed=True
        save(output/'committed-publication-resources.json',dict(database=database,database_oid=clone_oid,roles=role_oids,source_database=DATABASE))
        passed('isolated_disposable_clone_and_restricted_roles_committed_without_changing_retained_restore')
        def store(role):
            target=AccountDatabaseTarget('never-connected.invalid',database,role)
            return PostgresAccountDirectory('host=never-connected.invalid port=5432 dbname='+database+' user='+role+' sslmode=verify-full',settings,target)
        reader,writer=map(store,roles[:2]);gateway=PostgresAccountDataGateway(reader);service=DeploymentMonthlyInputs(gateway);publisher=DeploymentAccountPublisher(service,writer)
        def restricted(role,*,readonly):
            @contextmanager
            def opened():
                with psycopg.connect(make_conninfo(dbname=database,user='postgres',host='/var/run/postgresql',port='5432',connect_timeout='5',options='-c timezone=UTC')) as conn:
                    if readonly:conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                    conn.execute('SET LOCAL search_path=pg_catalog');conn.execute("SET LOCAL lock_timeout='5s'");conn.execute("SET LOCAL statement_timeout='30s'")
                    if conn.execute('SELECT current_database(),current_user,inet_server_addr()').fetchone()!=(database,'postgres',None):raise ValueError('Unexpected disposable target')
                    conn.execute(sql.SQL('SET LOCAL SESSION AUTHORIZATION {}').format(sql.Identifier(role)))
                    validate_runtime_role(conn,AccountDatabaseTarget('never-connected.invalid',database,role));m.verify_schema(conn)
                    yield conn;require_transaction(conn)
            return opened()
        @contextmanager
        def inputs_connection(*,readonly):
            with restricted(roles[0],readonly=readonly) as conn:yield conn
        @contextmanager
        def publisher_connection(*,readonly):
            with restricted(roles[1],readonly=readonly) as conn:yield conn
        user,foreign=map(ServerSession,identities[:2])
        with patch.object(reader,'_connection',new=inputs_connection),patch.object(writer,'_connection',new=publisher_connection):
            original=reader.snapshot(user);owner_id=original.user(identities[0]).id
            for name in ('A','B'):reader.execute(user,reader.snapshot(user).revision,'add_account',owner_id=owner_id,name=name,broker='schwab')
            a,b=(item.id for item in reader.snapshot(user).accounts if not item.legacy)
            drafts={};revisions={}
            for account,number in ((a,'12345678'),(b,'87654321')):
                gateway.bind(user,account,'securities',number,expected_revision=reader.snapshot(user).revision)
                draft=service.create(user,account,2025,1);revision=0
                for kind,upload in synthetic_sources(number).items():revision=service.save(user,account,draft.id,revision,kind,upload)
                drafts[account]=draft;revisions[account]=revision
            initial=snapshot();real_write=AppendOnlyArtifacts.write_text
            def fail_marker(storage,key,text):
                value=real_write(storage,key,text)
                if key.endswith('-finalized.json'):raise RuntimeError('injected marker failure')
                return value
            with patch.object(AppendOnlyArtifacts,'write_text',new=fail_marker):
                try:publisher.publish(user,a,drafts[a].id,revisions[a],confirm_publication=True,forex_applicable=False,crypto_applicable=False)
                except RuntimeError as error:
                    if str(error)!='injected marker failure':raise
                else:raise AssertionError('Injected failure missing')
            assert snapshot()==initial
            passed('failed_independent_publication_connection_rolls_back_every_generation_row')
            barrier=Barrier(3);marker=Event();release=Event()
            def paused(storage,key,text):
                value=real_write(storage,key,text)
                if key.endswith('-finalized.json'):
                    marker.set()
                    if not release.wait(15):raise RuntimeError('Reader coordination timed out')
                return value
            def attempt():
                barrier.wait(timeout=10)
                try:return ('won',publisher.publish(user,a,drafts[a].id,revisions[a],confirm_publication=True,forex_applicable=False,crypto_applicable=False))
                except ValueError as error:return ('blocked',str(error))
            with patch.object(AppendOnlyArtifacts,'write_text',new=paused),ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(attempt) for _ in range(2)]
                try:
                    barrier.wait(timeout=10)
                    if not marker.wait(10):raise RuntimeError('Publisher did not reach visibility marker')
                    with gateway.transaction(user,a) as old_snapshot:
                        assert old_snapshot.artifacts.list_keys()==()
                        release.set();results=[f.result(timeout=15) for f in futures]
                        assert old_snapshot.artifacts.list_keys()==()
                finally:release.set()
            assert sorted(item[0] for item in results)==['blocked','won']
            assert any('authoritative' in item[1] for item in results if item[0]=='blocked')
            passed('simultaneous_committed_duplicate_publications_have_exactly_one_winner_without_overwrite')
            passed('readonly_repeatable_reader_never_observes_uncommitted_or_mixed_generation')
            result=load_deployment_results(service,user,a,drafts[a].id,revisions[a])
            assert sum(float(q) for _,q in result.positions)==110 and result.original_documents==4
            after=snapshot();assert load_deployment_results(service,user,a,drafts[a].id,revisions[a])==result and snapshot()==after
            passed('independent_canonical_reader_verifies_committed_110_share_generation_and_original_receipt_without_writes')
            with gateway.transaction(user,a) as readonly:
                assert readonly._connection.execute('SHOW transaction_read_only').fetchone()==('on',)
                try:
                    with readonly._connection.transaction():readonly._connection.execute('UPDATE campaigniq_accounts.import_drafts SET revision=revision WHERE false')
                except psycopg.errors.ReadOnlySqlTransaction:pass
                else:raise AssertionError('Reader accepted a raw SQL write')
            from campaigniq.persistence.account_data_session import ClosedAccountSession
            try:readonly.artifacts.list_keys()
            except ClosedAccountSession:pass
            else:raise AssertionError('Read handle escaped snapshot lifetime')
            assert snapshot()==after
            passed('reader_database_transaction_is_readonly_and_handles_expire_without_row_changes')

            with gateway.transaction(user,b) as session:assert session.artifacts.list_keys()==()
            for actor,account in ((foreign,a),(user,b)):
                try:load_deployment_results(service,actor,account,drafts[a].id,revisions[a])
                except (PermissionError,FileNotFoundError,ValueError):pass
                else:raise AssertionError('Unauthorized or foreign draft returned a result')
            assert snapshot()==after
            passed('foreign_actor_and_foreign_draft_cannot_read_committed_results_other_account_stays_empty')
            feb=service.create(user,a,2025,2);revision=0
            for kind,upload in february_sources().items():revision=service.save(user,a,feb.id,revision,kind,upload)
            publisher.publish(user,a,feb.id,revision,confirm_publication=True,forex_applicable=False,crypto_applicable=False)
            feb_result=load_deployment_results(service,user,a,feb.id,revision)
            assert sum(float(q) for _,q in feb_result.positions)==115
            assert load_deployment_results(service,user,a,drafts[a].id,revisions[a])==result
            passed('following_month_commits_from_published_opening_and_prior_generation_remains_identical')
            foreign_id=reader.snapshot(user).user(identities[1]).id
            reader.execute(user,reader.snapshot(user).revision,'set_grant',user_id=foreign_id,account_id=a,permission=Permission.VIEW)
            assert load_deployment_results(service,foreign,a,drafts[a].id,revisions[a])==result
            revoke_revision=reader.snapshot(user).revision;revoking=Event()
            def revoke():
                revoking.set()
                return reader.execute(user,revoke_revision,'set_grant',user_id=foreign_id,account_id=a,permission=None)
            with ThreadPoolExecutor(max_workers=1) as pool:
                with gateway.transaction(foreign,a):
                    future=pool.submit(revoke)
                    if not revoking.wait(5):raise RuntimeError('Revocation worker did not start')
                    try:future.result(timeout=0.15)
                    except TimeoutError:pass
                    else:raise AssertionError('Revocation committed during active snapshot')
                future.result(timeout=10)
            try:load_deployment_results(service,foreign,a,drafts[a].id,revisions[a])
            except PermissionError:pass
            else:raise AssertionError('Revoked reader retained access')
            passed('committed_view_revocation_waits_for_active_reader_and_blocks_later_requests')
            receipt_key='2025-01-account-publication-receipt.json';scope=reader.authorize(user,a).storage_scope
            with owner() as conn:receipt=conn.execute('SELECT content FROM campaigniq_accounts.artifact_storage_dev WHERE namespace=%s AND artifact_key=%s',(scope,receipt_key)).fetchone()[0]
            prior=snapshot()
            try:
                changed=json.loads(receipt);changed['draft_revision']=999
                with owner() as conn:conn.execute('UPDATE campaigniq_accounts.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',(json.dumps(changed),scope,receipt_key))
                try:load_deployment_results(service,user,a,drafts[a].id,revisions[a])
                except ValueError:pass
                else:raise AssertionError('Conflicting source receipt returned a result')
            finally:
                with owner() as conn:conn.execute('UPDATE campaigniq_accounts.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',(receipt,scope,receipt_key))
            assert snapshot()==prior
            passed('independently_committed_receipt_conflict_withholds_entire_result_without_reader_repair')

            if verify_preferences or verify_application:
                from types import SimpleNamespace
                if verify_application:
                    from campaigniq.account_deployment_application_rehearsal import verify_application as verify_personal
                else:
                    from campaigniq.account_deployment_preferences_rehearsal import verify_preferences as verify_personal
                preference_store=store(roles[2])
                @contextmanager
                def preference_connection(*,readonly):
                    with restricted(roles[2],readonly=readonly) as conn:yield conn
                with patch.object(preference_store,'_connection',new=preference_connection):
                    verify_personal(SimpleNamespace(store=preference_store,reader=reader,writer=writer,user=user,foreign=foreign,
                        accounts=(a,b),drafts=drafts,revisions=revisions,identities=identities,owner=owner,snapshot=snapshot,restricted=restricted,roles=roles),passed)
            if verify_dashboard:
                from types import SimpleNamespace
                from campaigniq.account_deployment_dashboard_rehearsal import verify_parity
                verify_parity(SimpleNamespace(reader=reader,inputs=service,publisher=publisher,user=user,foreign=foreign,
                    identities=identities,accounts=(a,b),drafts=drafts,revisions=revisions,owner=owner,snapshot=snapshot),passed)
            if verify_futures:
                from types import SimpleNamespace
                from campaigniq.account_futures_publication_rehearsal import verify_futures as check_futures
                check_futures(SimpleNamespace(reader=reader,inputs=service,publisher=publisher,user=user,foreign=foreign,
                    identities=identities,owner=owner,snapshot=snapshot),passed)
            with owner() as conn:assert {k:v for k,v in inventory(conn).items() if not k.startswith(m.SCHEMA+'.')}==before
            passed('every_copied_existing_financial_document_preference_and_directory_row_unchanged')
        report['passed']=True
    except Exception as error:report['error_type']=type(error).__name__;raise
    finally:
        try:
            if clone_oid is not None and control is not None:
                # Failed setup can roll back newly created roles; never adopt a
                # replacement OID or a role which did not actually commit.
                existing={}
                for role,oid in role_oids.items():
                    actual=control.execute('SELECT oid FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
                    if actual is None and not roles_committed:continue
                    if actual!=(oid,):raise ValueError('Temporary role identity changed; clone retained for recovery')
                    existing[role]=oid
                cleanup_clone(control,database,clone_oid,existing,before)
                report['disposable_database_and_roles_removed']=True
            if before is not None:
                with psycopg.connect(source_dsn) as observer:
                    observer.execute('SET TRANSACTION READ ONLY')
                    assert inventory(observer)==before and role_catalog_digest(observer)==before_acl
                if control is not None:
                    assert not control.execute('SELECT 1 FROM pg_database WHERE datname=%s',(database,)).fetchone()
                    for role in roles:assert not control.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
                if report['passed']:passed('independent_reader_confirms_retained_restore_unchanged_and_only_disposable_database_roles_removed')
        except Exception as cleanup_error:
            report.update(passed=False,cleanup_error_type=type(cleanup_error).__name__);raise
        finally:
            if control is not None:control.close()
            save(output/report_name,report);print('Report:',output/report_name,flush=True)
    print('Retained LOCAL restore unchanged. Committed tests used a disposable clone, now removed. Production unchanged.')
    return report
