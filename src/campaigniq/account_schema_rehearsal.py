"""Rehearse versioned DDL only on the pinned LOCAL full restore; always roll back."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pwd
from uuid import uuid4
from unittest.mock import patch
from campaigniq import account_schema_migrations as migration

DATABASE = 'campaigniq_restore_20261024_194114'


def restore_conninfo(*, development_only):
    if development_only is not True or any(os.environ.get(k) for k in
       ('RENDER','RENDER_SERVICE_ID','PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGOPTIONS')):
        raise ValueError('Explicit local restore rehearsal required; routing overrides blocked')
    from psycopg.conninfo import make_conninfo
    return make_conninfo(dbname=DATABASE,user=pwd.getpwuid(os.getuid()).pw_name,
       host='/var/run/postgresql',port='5432',connect_timeout='5',
       application_name='campaigniq_account_schema_rehearsal',options='-c timezone=UTC')


def inventory(conn, *, lock=False):
    from psycopg import sql
    tables=conn.execute("""SELECT n.nspname,c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind='r' AND n.nspname NOT LIKE 'pg_%' AND n.nspname!='information_schema'
        ORDER BY 1,2""").fetchall()
    if lock:
        for schema,name in tables:
            conn.execute(sql.SQL('LOCK TABLE {}.{} IN SHARE MODE').format(sql.Identifier(schema),sql.Identifier(name)))
    result={}
    for schema,name in tables:
        rows=conn.execute(sql.SQL('SELECT md5(row_to_json(t)::text),count(*) FROM {}.{} t GROUP BY 1 ORDER BY 1')
                          .format(sql.Identifier(schema),sql.Identifier(name))).fetchall()
        result[schema+'.'+name]=migration.digest(rows)
    return result


def run_rehearsal(output, *, development_only):
    import psycopg
    conninfo=restore_conninfo(development_only=development_only)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    report=dict(format='campaigniq.account_schema_rehearsal',version=1,database=DATABASE,
       checks={},migration_source_sha256=migration.SOURCE_SHA256,production_connection_made=False,
       account_mode_activated=False)
    def passed(name):
        report['checks'][name]=True
        print(name.replace('_',' ')+': PASS',flush=True)
    try:
        with psycopg.connect(conninfo,autocommit=True) as conn:
            actual=conn.execute('SELECT current_database(),current_user,inet_server_addr()').fetchone()
            if actual!=(DATABASE,pwd.getpwuid(os.getuid()).pw_name,None):
                raise ValueError('Unexpected database target')
            if conn.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(migration.SCHEMA,)).fetchone():
                raise ValueError('Restore already contains deployment schema; no overwrite attempted')
            with conn.transaction(force_rollback=True):
                conn.execute("SET LOCAL lock_timeout='5s'")
                conn.execute("SET LOCAL statement_timeout='30s'")
                before=inventory(conn,lock=True)
                if not {'public.artifact_storage_dev','public.import_drafts','public.import_draft_documents'}<=set(before):
                    raise ValueError('Expected full workspace restore tables missing')
                # A failed migration must unwind both DDL and its ledger.
                try:
                    with conn.transaction():
                        with patch.object(migration,'catalog_digest',side_effect=RuntimeError('injected migration failure')):
                            migration.apply_migrations(conn)
                except RuntimeError as error:
                    if str(error)!='injected migration failure':raise
                else:raise AssertionError('Injection did not fail')
                assert not conn.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(migration.SCHEMA,)).fetchone()
                assert inventory(conn)==before
                passed('injected_schema_creation_failure_rolls_back_ddl_ledger_and_existing_rows')
                assert migration.apply_migrations(conn) is True
                signature=migration.catalog_digest(conn)
                assert migration.apply_migrations(conn) is False
                assert migration.catalog_digest(conn)==signature
                passed('versioned_schema_applies_once_and_repeat_is_unchanged')
                try:
                    with conn.transaction():
                        conn.execute('ALTER TABLE campaigniq_accounts.directories ADD COLUMN unexpected text')
                        migration.apply_migrations(conn)
                except ValueError:pass
                else:raise AssertionError('Catalog drift was accepted')
                assert migration.catalog_digest(conn)==signature
                passed('schema_drift_blocks_reapplication_without_repair')
                try:
                    with conn.transaction():
                        conn.execute("UPDATE campaigniq_accounts.schema_migrations SET source_sha256=repeat('0',64)")
                        migration.apply_migrations(conn)
                except ValueError:pass
                else:raise AssertionError('Changed ledger accepted')
                passed('changed_migration_checksum_blocks_reapplication')
                # PUBLIC has no explicit schema/table ACL entry. Runtime role grants
                # are deliberately deferred to the reviewed activation migration.
                public=conn.execute("""SELECT count(*) FROM pg_namespace n,
                    LATERAL aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner))) a
                    WHERE n.nspname=%s AND a.grantee=0""",(migration.SCHEMA,)).fetchone()[0]
                assert public==0
                public=conn.execute("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
                    LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
                    WHERE n.nspname=%s AND c.relkind='r' AND a.grantee=0""",(migration.SCHEMA,)).fetchone()[0]
                assert public==0
                passed('deployment_schema_and_tables_grant_no_public_access')
                after=inventory(conn)
                assert {k:v for k,v in after.items() if not k.startswith(migration.SCHEMA+'.')}==before
                for table in migration.TABLES-{'schema_migrations'}:
                    assert conn.execute('SELECT count(*) FROM campaigniq_accounts.'+table).fetchone()[0]==0
                passed('existing_financial_documents_preferences_and_directory_rows_unchanged_no_registry_copied')
            # Independent connection after outer rollback verifies no durable DDL.
        with psycopg.connect(conninfo) as independent:
            independent.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            assert not independent.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(migration.SCHEMA,)).fetchone()
            assert inventory(independent)==before
        passed('independent_reader_confirms_exact_table_rows_and_schema_rollback')
        report['passed']=True
    except Exception as error:
        report.update(passed=False,error_type=type(error).__name__)
        raise
    finally:
        with (output/'account-schema-report.json').open('x') as handle:
            json.dump(report,handle,indent=2,sort_keys=True);handle.write('\n');handle.flush();os.fsync(handle.fileno())
        print('Report:',output/'account-schema-report.json')
    print('Local full restore unchanged. No registry bootstrap, account activation or production connection.')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only',action='store_true',required=True)
    args=parser.parse_args()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    output=Path.home()/'CampaignIQ-Records'/'Development-Rehearsals'/('Account-schema-'+stamp)
    run_rehearsal(output,development_only=args.development_only)


if __name__=='__main__':main()
