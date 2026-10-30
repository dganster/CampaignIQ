"""Rehearse runtime grants only on the fixed local restore; roll back all DDL/roles."""
import argparse
from datetime import datetime,timezone
import os
from pathlib import Path
import pwd
from types import SimpleNamespace
from uuid import uuid4
from campaigniq import account_schema_migrations as migration
from campaigniq.account_runtime_roles import review_runtime_grants,apply_runtime_grants,role_catalog_digest
from campaigniq.account_schema_rehearsal import restore_conninfo,inventory,DATABASE
from campaigniq.account_data_backend_rehearsal import save
from campaigniq.persistence.deployment_account_directory import validate_runtime_role
from campaigniq.persistence.deployment_account_data import validate_data_role


def run_rehearsal(output,*,development_only):
    import psycopg
    from psycopg import sql
    dsn=restore_conninfo(development_only=development_only)
    role='ciq_role_rehearsal_'+uuid4().hex[:16]
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    report=dict(format='campaigniq.runtime-role-rehearsal',version=1,database=DATABASE,checks={},
        production_connection_made=False,account_mode_activated=False,passed=False)
    def passed(name):report['checks'][name]=True;print(name.replace('_',' ')+': PASS',flush=True)
    try:
        with psycopg.connect(dsn,autocommit=True) as conn:
            actual=conn.execute('SELECT current_database(),current_user,inet_server_addr()').fetchone()
            if actual!=(DATABASE,pwd.getpwuid(os.getuid()).pw_name,None):raise ValueError('Unexpected local restore target')
            # SESSION AUTHORIZATION lets the actual validator see the restricted
            # session user too. This requirement is only for the rollback runner.
            if conn.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user').fetchone()!=(True,):
                raise PermissionError('Local rehearsal requires restore superuser for temporary session authorization; nothing changed')
            if conn.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(migration.SCHEMA,)).fetchone():
                raise ValueError('Restore already has deployment schema; nothing adopted')
            with conn.transaction(force_rollback=True):
                conn.execute("SET LOCAL lock_timeout='5s'")
                conn.execute("SET LOCAL statement_timeout='30s'")
                before=inventory(conn,lock=True)
                before_acl=role_catalog_digest(conn)
                save(output/'prewrite-recovery-receipt.json',dict(database=DATABASE,original_table_sha256=before,
                    temporary_role=role,original_catalog_sha256=before_acl,transaction_policy='forced rollback of all roles grants schema and rows'))
                migration.apply_migrations(conn)
                conn.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS').format(sql.Identifier(role)))
                plan=review_runtime_grants(conn,role)
                assert review_runtime_grants(conn,role)==plan
                save(output/'reviewed-grant-plan.json',plan)
                passed('explicit_role_review_is_deterministic_and_changes_no_financial_rows')
                try:
                    with conn.transaction():
                        apply_runtime_grants(conn,role,reviewed_sha256=migration.digest(plan))
                        raise RuntimeError('injected post grant failure')
                except RuntimeError as error:
                    if str(error)!='injected post grant failure':raise
                assert review_runtime_grants(conn,role)==plan
                passed('injected_post_grant_failure_rolls_back_grants_and_migration_ledger')
                try:
                    with conn.transaction():apply_runtime_grants(conn,role,reviewed_sha256='0'*64)
                except ValueError:pass
                else:raise AssertionError('Stale review accepted')
                assert review_runtime_grants(conn,role)==plan
                passed('changed_review_is_rejected_before_grant_changes')
                assert apply_runtime_grants(conn,role,reviewed_sha256=migration.digest(plan))
                again=review_runtime_grants(conn,role)
                assert apply_runtime_grants(conn,role,reviewed_sha256=migration.digest(again)) is False
                migration.verify_schema(conn)
                passed('versioned_grants_apply_once_and_exact_repeat_changes_nothing')
                with conn.transaction(force_rollback=True):
                    conn.execute(sql.SQL('SET LOCAL SESSION AUTHORIZATION {}').format(sql.Identifier(role)))
                    validate_runtime_role(conn,SimpleNamespace(database=DATABASE,user=role))
                    validate_data_role(conn);migration.verify_schema(conn)
                    # Allowed statements resolve every required relation, with no
                    # synthetic financial records or directory bootstrap needed.
                    for table in ('directories','audit_events','schema_migrations','artifact_storage_dev','import_drafts','import_draft_documents'):
                        conn.execute('SELECT * FROM campaigniq_accounts.'+table+' LIMIT 0')
                    conn.execute('INSERT INTO campaigniq_accounts.audit_events(registry_id,sequence,event,event_sha256) SELECT NULL,0,NULL,NULL WHERE false')
                    conn.execute('INSERT INTO campaigniq_accounts.import_drafts(id,workspace,year,month) SELECT NULL,NULL,2025,1 WHERE false')
                    conn.execute('INSERT INTO campaigniq_accounts.import_draft_documents(id,draft_id,role,filename,media_type,sha256,content,revision) SELECT NULL,NULL,NULL,NULL,NULL,NULL,NULL,0 WHERE false')
                    conn.execute('UPDATE campaigniq_accounts.directories SET revision=revision WHERE false')
                    conn.execute('UPDATE campaigniq_accounts.import_drafts SET revision=revision WHERE false')
                    conn.execute('UPDATE campaigniq_accounts.import_draft_documents SET revision=revision WHERE false')
                    passed('actual_restricted_session_passes_existing_runtime_and_input_role_checks')
                    forbidden=(
                        'DELETE FROM campaigniq_accounts.directories WHERE false',
                        'INSERT INTO campaigniq_accounts.directories(registry_id,revision,document,document_sha256) SELECT NULL,0,NULL,NULL WHERE false',
                        'UPDATE campaigniq_accounts.audit_events SET sequence=sequence WHERE false',
                        'DELETE FROM campaigniq_accounts.audit_events WHERE false',
                        'UPDATE campaigniq_accounts.schema_migrations SET version=version WHERE false',
                        'DELETE FROM campaigniq_accounts.artifact_storage_dev WHERE false',
                        'UPDATE campaigniq_accounts.artifact_storage_dev SET content=content WHERE false',
                        'TRUNCATE campaigniq_accounts.import_drafts',
                        'UPDATE public.artifact_storage_dev SET content=content WHERE false',
                        'DELETE FROM public.import_drafts WHERE false',
                        'UPDATE public.import_draft_documents SET revision=revision WHERE false',
                        'SELECT * FROM campaigniq_accounts.account_preferences LIMIT 0',
                        'CREATE TABLE campaigniq_accounts.forbidden_role_table(id integer)',
                        'CREATE TABLE public.forbidden_role_table(id integer)',
                    )
                    for statement in forbidden:
                        try:
                            with conn.transaction():conn.execute(statement)
                        except psycopg.errors.InsufficientPrivilege:pass
                        else:raise AssertionError('Restricted role accepted prohibited statement')
                    passed('database_denies_legacy_writes_publication_audit_rewrites_ledger_writes_and_ddl')
                # Drift cannot be blessed by a repeat grant operation.
                for grant in ('GRANT INSERT ON campaigniq_accounts.artifact_storage_dev TO {}',
                              'GRANT UPDATE(content) ON public.artifact_storage_dev TO {}',
                              'GRANT UPDATE(content) ON campaigniq_accounts.artifact_storage_dev TO {}'):
                    with conn.transaction(force_rollback=True):
                        conn.execute(sql.SQL(grant).format(sql.Identifier(role)))
                        try:review_runtime_grants(conn,role)
                        except (ValueError,PermissionError):pass
                        else:raise AssertionError('Privilege drift accepted')
                    migration.verify_schema(conn)
                passed('unexpected_privilege_drift_blocks_review_without_automatic_repair')
                assert {k:v for k,v in inventory(conn).items() if not k.startswith(migration.SCHEMA+'.')}==before
                passed('all_existing_financial_original_document_preference_and_directory_rows_unchanged')
        with psycopg.connect(dsn) as observer:
            observer.execute('SET TRANSACTION READ ONLY')
            assert inventory(observer)==before
            assert role_catalog_digest(observer)==before_acl
            assert not observer.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(migration.SCHEMA,)).fetchone()
            assert not observer.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
        passed('independent_reader_confirms_original_restore_and_temporary_role_schema_rollback')
        report['passed']=True
    except Exception as error:
        report['error_type']=type(error).__name__;raise
    finally:
        save(output/'account-runtime-roles-report.json',report)
        print('Report:',output/'account-runtime-roles-report.json',flush=True)
    print('Local full restore unchanged. Temporary role and grants rolled back. Production unchanged.')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only',action='store_true',required=True)
    args=parser.parse_args()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    run_rehearsal(Path.home()/'CampaignIQ-Records/Development-Rehearsals'/('Account-runtime-roles-'+stamp),development_only=args.development_only)


if __name__=='__main__':main()
