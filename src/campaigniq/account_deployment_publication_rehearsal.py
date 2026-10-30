"""Restricted input + append-only publication roles on LOCAL restore; rollback all."""
import argparse
from contextlib import contextmanager
from datetime import datetime,timezone
import json,os,pwd
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from campaigniq import account_schema_migrations as m
from campaigniq.account_runtime_roles import review_runtime_grants,apply_runtime_grants,role_catalog_digest
from campaigniq.account_publication_roles import review_publication_grants,apply_publication_grants
from campaigniq.account_schema_rehearsal import restore_conninfo,inventory,DATABASE
from campaigniq.account_data_backend_rehearsal import save,schema_rows
from campaigniq.account_backend_rehearsal import ServerSession
from campaigniq.account_administration import Identity,LegacyOwner,Permission
from campaigniq.account_backend_config import AccountDirectorySettings,AccountDatabaseTarget
from campaigniq.account_directory_bootstrap import review_bootstrap,bootstrap_directory
from campaigniq.persistence.deployment_account_directory import PostgresAccountDirectory,DirectoryTransaction,validate_runtime_role
from campaigniq.persistence.deployment_account_data import PostgresAccountDataGateway,validate_data_role
from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
from campaigniq.account_deployment_publication import DeploymentAccountPublisher
from campaigniq.account_selection_rehearsal import synthetic_sources
from campaigniq.persistence.lot_book_store import load_lot_book_from_storage


def run_rehearsal(output,*,development_only):
    import psycopg
    from psycopg import sql
    dsn=restore_conninfo(development_only=development_only)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    report=dict(format='campaigniq.deployment-publication-rehearsal',version=1,database=DATABASE,checks={},passed=False,production_connection_made=False,account_mode_activated=False)
    def passed(name):report['checks'][name]=True;print(name.replace('_',' ')+': PASS',flush=True)
    try:
        with psycopg.connect(dsn,autocommit=True) as conn:
            if conn.execute('SELECT current_database(),current_user,inet_server_addr()').fetchone()!=(DATABASE,pwd.getpwuid(os.getuid()).pw_name,None):raise ValueError('Unexpected local restore')
            if conn.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user').fetchone()!=(True,):raise PermissionError('Use the local administrator launcher')
            if conn.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(m.SCHEMA,)).fetchone():raise ValueError('Existing account schema; stopped')
            roles=['ciq_inputs_'+uuid4().hex[:16],'ciq_publish_'+uuid4().hex[:16]]
            with conn.transaction(force_rollback=True):
                conn.execute("SET LOCAL lock_timeout='5s'");conn.execute("SET LOCAL statement_timeout='30s'")
                before=inventory(conn,lock=True);before_acl=role_catalog_digest(conn)
                save(output/'prewrite-recovery-receipt.json',dict(database=DATABASE,original_table_sha256=before,original_catalog_sha256=before_acl,temporary_roles=roles,transaction_policy='forced rollback'))
                m.apply_migrations(conn)
                for role in roles:conn.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS').format(sql.Identifier(role)))
                plan=review_runtime_grants(conn,roles[0]);apply_runtime_grants(conn,roles[0],reviewed_sha256=m.digest(plan))
                save(output/'reviewed-grant-plan.json',plan)
                plan=review_publication_grants(conn,roles[1]);save(output/'reviewed-publication-grant-plan.json',plan)
                try:
                    with conn.transaction():
                        apply_publication_grants(conn,roles[1],reviewed_sha256=m.digest(plan));raise RuntimeError('injected grants')
                except RuntimeError as e:
                    if str(e)!='injected grants':raise
                assert review_publication_grants(conn,roles[1])==plan
                apply_publication_grants(conn,roles[1],reviewed_sha256=m.digest(plan))
                again=review_publication_grants(conn,roles[1]);assert apply_publication_grants(conn,roles[1],reviewed_sha256=m.digest(again)) is False
                passed('separate_append_only_publication_grants_are_reviewed_idempotent_and_rollback_on_failure')
                scopes=tuple(r[0] for r in conn.execute("SELECT DISTINCT namespace FROM public.artifact_storage_dev WHERE namespace LIKE 'workspace-%' ORDER BY 1").fetchall())
                if len(scopes)<2:raise ValueError('Two retained owner namespaces required')
                issuer='https://deployment-publication-rehearsal.invalid';identities=tuple(Identity(issuer,'synthetic-'+str(i)) for i in range(len(scopes)))
                settings=AccountDirectorySettings(str(uuid4()),issuer,tuple(sorted((i.subject,w) for i,w in zip(identities,scopes))))
                owners=tuple(LegacyOwner(i,w,'Synthetic owner','Retained account','schwab') for i,w in zip(identities,scopes))
                bootstrap=review_bootstrap(conn,settings,owners,identities[0]);bootstrap_directory(conn,settings,bootstrap,reviewed_sha256=bootstrap.sha256)
                user,foreign=map(ServerSession,identities[:2])
                tx=DirectoryTransaction(conn,settings);owner=tx.snapshot(user).user(identities[0]).id
                for name in ('A','B'):tx.execute(user,tx.snapshot(user).revision,'add_account',owner_id=owner,name=name,broker='schwab')
                a,b=(x.id for x in tx.snapshot(user).accounts if not x.legacy)
                def store(role):
                    target=AccountDatabaseTarget('never-connected.invalid',DATABASE,role)
                    return PostgresAccountDirectory('host=never-connected.invalid port=5432 dbname='+DATABASE+' user='+role+' sslmode=verify-full',settings,target)
                reader,writer=map(store,roles);gateway=PostgresAccountDataGateway(reader);service=DeploymentMonthlyInputs(gateway);publisher=DeploymentAccountPublisher(service,writer)
                def connection(role,*,readonly):
                    @contextmanager
                    def opened():
                        with conn.transaction():
                            conn.execute(sql.SQL('SET LOCAL SESSION AUTHORIZATION {}').format(sql.Identifier(role)))
                            validate_runtime_role(conn,AccountDatabaseTarget('never-connected.invalid',DATABASE,role));m.verify_schema(conn)
                            try:yield conn
                            finally:
                                # A failed SQL savepoint must roll back before any
                                # further command; rollback also restores auth.
                                from psycopg.pq import TransactionStatus
                                if conn.info.transaction_status==TransactionStatus.INTRANS:
                                    conn.execute('SET LOCAL SESSION AUTHORIZATION DEFAULT')
                    return opened()
                @contextmanager
                def read_connection(*,readonly):
                    with connection(roles[0],readonly=readonly) as value:yield value
                @contextmanager
                def write_connection(*,readonly):
                    with connection(roles[1],readonly=readonly) as value:yield value
                with patch.object(reader,'_connection',new=read_connection),patch.object(writer,'_connection',new=write_connection):
                    drafts={};revisions={}
                    for account,number in ((a,'12345678'),(b,'87654321')):
                        gateway.bind(user,account,'securities',number,expected_revision=tx.snapshot(user).revision)
                        draft=service.create(user,account,2025,1);revision=0
                        for kind,upload in synthetic_sources(number).items():revision=service.save(user,account,draft.id,revision,kind,upload)
                        drafts[account]=draft;revisions[account]=revision
                    original_documents=conn.execute('SELECT id,sha256,content FROM campaigniq_accounts.import_draft_documents ORDER BY id').fetchall()
                    initial=schema_rows(conn)
                    def publish(account=a,actor=user,revision=None,confirmed=True):
                        return publisher.publish(actor,account,drafts[account].id,revisions[account] if revision is None else revision,confirm_publication=confirmed,forex_applicable=False,crypto_applicable=False)
                    def rejected(kind,call):
                        try:
                            with conn.transaction():call()
                        except kind:pass
                        else:raise AssertionError('Expected publication rejection')
                        assert schema_rows(conn)==initial
                    rejected(PermissionError,lambda:publish(confirmed=False));rejected(ValueError,lambda:publish(revision=0));rejected(PermissionError,lambda:publish(actor=foreign))
                    passed('confirmation_current_revision_and_current_owner_are_required_without_row_changes')
                    def view_publication():
                        tx.execute(user,tx.snapshot(user).revision,'set_grant',user_id=tx.snapshot(user).user(identities[1]).id,account_id=a,permission=Permission.VIEW)
                        publish(actor=foreign)
                    rejected(PermissionError,view_publication)
                    def archived_publication():
                        tx.execute(user,tx.snapshot(user).revision,'configure_account',account_id=a,archived=True)
                        publish()
                    rejected(PermissionError,archived_publication)
                    def mismatch():
                        from campaigniq.import_draft_bridge import RestoredUpload
                        upload=synthetic_sources('87654321')['schwab_brokerage_statement']
                        changed=RestoredUpload(upload.name,upload.media_type,upload.content.replace(b'110.0000',b'111.0000'))
                        revision=service.save(user,b,drafts[b].id,revisions[b],'schwab_brokerage_statement',changed,replace=True)
                        publisher.publish(user,b,drafts[b].id,revision,confirm_publication=True,forex_applicable=False,crypto_applicable=False)
                    rejected(ValueError,mismatch)
                    passed('view_access_archive_and_independent_closing_mismatch_cannot_publish')

                    from campaigniq.account_deployment_publication import AppendOnlyArtifacts
                    real=AppendOnlyArtifacts.write_text
                    def failing(storage,key,text):
                        result=real(storage,key,text)
                        if key.endswith('-finalized.json'):raise RuntimeError('injected marker failure')
                        return result
                    with patch.object(AppendOnlyArtifacts,'write_text',new=failing):rejected(RuntimeError,lambda:publish())

                    def caught_sql(storage,key,text):
                        result=real(storage,key,text)
                        if key.endswith('-finalized.json'):
                            try:storage._session._connection.execute('SELECT 1/0')
                            except psycopg.Error:pass
                        return result
                    with patch.object(AppendOnlyArtifacts,'write_text',new=caught_sql):rejected(RuntimeError,lambda:publish())
                    passed('injected_post_marker_failure_rolls_back_every_generation_row')
                    published=publish();assert published.revision==4
                    with gateway.transaction(user,a) as session:
                        book=load_lot_book_from_storage(session.artifacts,'2025-01-lot-book.json')
                        assert sum(lot.quantity for lots in book.lot_book._lots.values() for lot in lots)==110
                        receipt=json.loads(session.artifacts.read_text('2025-01-account-publication-receipt.json'))
                        assert receipt['format']=='campaigniq.deployment_account_publication' and len(receipt['original_documents'])==4
                        assert receipt['actor_id']==owner
                    assert conn.execute('SELECT id,sha256,content FROM campaigniq_accounts.import_draft_documents ORDER BY id').fetchall()==original_documents
                    passed('restricted_writer_produces_110_share_generation_and_exact_original_source_receipt')
                    initial=schema_rows(conn);rejected(ValueError,lambda:publish())
                    with gateway.transaction(user,b) as session:assert not session.artifacts.list_keys()
                    passed('published_generation_cannot_be_replaced_and_other_account_remains_empty')
                    january=conn.execute("SELECT artifact_key,content FROM campaigniq_accounts.artifact_storage_dev WHERE namespace=%s ORDER BY artifact_key",(tx.authorize(user,a).storage_scope,)).fetchall()
                    from campaigniq.account_publication_rehearsal import february_sources
                    feb=service.create(user,a,2025,2);revision=0
                    for kind,upload in february_sources().items():revision=service.save(user,a,feb.id,revision,kind,upload)
                    publisher.publish(user,a,feb.id,revision,confirm_publication=True,forex_applicable=False,crypto_applicable=False)
                    with gateway.transaction(user,a) as session:
                        state=load_lot_book_from_storage(session.artifacts,'2025-02-lot-book.json')
                        assert sum(lot.quantity for lots in state.lot_book._lots.values() for lot in lots)==115
                        assert all(session.artifacts.read_text(key)==text for key,text in january)
                    initial=schema_rows(conn)
                    for year,month in ((2024,12),(2025,4)):
                        def prohibited_month(year=year,month=month):
                            draft=service.create(user,a,year,month)
                            publisher.publish(user,a,draft.id,0,confirm_publication=True,forex_applicable=False,crypto_applicable=False)
                        rejected(ValueError,prohibited_month)
                    passed('next_month_uses_published_opening_and_preserves_prior_generation_backfill_and_gap_blocked')

                    with read_connection(readonly=False) as session_conn:
                        validate_data_role(session_conn)
                        try:
                            with session_conn.transaction():session_conn.execute("INSERT INTO campaigniq_accounts.artifact_storage_dev VALUES('forbidden','forbidden','forbidden',now())")
                        except psycopg.errors.InsufficientPrivilege:pass
                        else:raise AssertionError('Ordinary role wrote artifacts')
                    with write_connection(readonly=False) as session_conn:
                        for statement in ('DELETE FROM campaigniq_accounts.artifact_storage_dev WHERE false','UPDATE campaigniq_accounts.artifact_storage_dev SET content=content WHERE false','UPDATE public.artifact_storage_dev SET content=content WHERE false','UPDATE campaigniq_accounts.import_draft_documents SET revision=revision WHERE false'):
                            try:
                                with session_conn.transaction():session_conn.execute(statement)
                            except psycopg.errors.InsufficientPrivilege:pass
                            else:raise AssertionError('Writer exceeded append-only scope')
                    passed('ordinary_inputs_cannot_publish_and_writer_cannot_rewrite_delete_originals_or_legacy_data')
                assert {k:v for k,v in inventory(conn).items() if not k.startswith(m.SCHEMA+'.')}==before
                passed('all_existing_financial_original_preference_and_directory_rows_unchanged')
        with psycopg.connect(dsn) as observer:
            observer.execute('SET TRANSACTION READ ONLY')
            assert inventory(observer)==before and role_catalog_digest(observer)==before_acl
            assert not observer.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(m.SCHEMA,)).fetchone()
            for role in roles:assert not observer.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
        passed('independent_reader_confirms_all_original_rows_and_roles_schema_grants_rolled_back')
        report['passed']=True
    except Exception as error:report['error_type']=type(error).__name__;raise
    finally:save(output/'account-deployment-publication-report.json',report);print('Report:',output/'account-deployment-publication-report.json',flush=True)
    print('Local full restore unchanged. Synthetic publication, roles and grants rolled back. Production unchanged.')
    return report
