"""Reviewed, caller-transaction role grants for deployment inputs; no connections.

Does not create roles, set passwords, activate accounts or grant publication.
Version 1 DDL remains immutable; version 2 records the reviewed ACL transition.
"""
import re
from campaigniq import account_schema_migrations as migration
from campaigniq.persistence.deployment_account_directory import require_transaction

VERSION=2
POLICY=(
 ('campaigniq_accounts','directories',('SELECT','UPDATE')),
 ('campaigniq_accounts','audit_events',('SELECT','INSERT')),
 ('campaigniq_accounts','schema_migrations',('SELECT',)),
 ('campaigniq_accounts','account_preferences',()),
 ('campaigniq_accounts','artifact_storage_dev',('SELECT',)),
 ('campaigniq_accounts','import_drafts',('SELECT','INSERT','UPDATE')),
 ('campaigniq_accounts','import_draft_documents',('SELECT','INSERT','UPDATE')),
 ('public','artifact_storage_dev',('SELECT',)),
 ('public','import_drafts',('SELECT',)),
 ('public','import_draft_documents',('SELECT',)),
)
PRIVILEGES=('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER')
SOURCE_SHA256=migration.digest(dict(version=VERSION,policy=POLICY,schemas=('public','campaigniq_accounts'),
    preconditions='explicit existing nonowner role without elevated flags or memberships',
    protocol='review exact role OID, database, schema/table/column ACLs and existing effective grants; grant subset; verify; append ledger'))


def role_name(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}',value) or value.startswith('pg_') or value=='public':
        raise ValueError('Explicit simple runtime role name required')
    return value


def _role_state(conn,role,policy=POLICY):
    role_name(role)
    row=conn.execute('''SELECT oid,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls
        FROM pg_roles WHERE rolname=%s''',(role,)).fetchone()
    if row is None or any(row[1:]):raise PermissionError('Existing unprivileged runtime role required')
    if conn.execute('SELECT 1 FROM pg_auth_members WHERE member=%s LIMIT 1',(row[0],)).fetchone():
        raise PermissionError('Runtime role memberships are prohibited')
    if conn.execute('SELECT has_database_privilege(%s,current_database(),%s)',(role,'CREATE')).fetchone()!=(False,):
        raise PermissionError('Runtime role cannot create database objects')
    for schema in ('public','campaigniq_accounts'):
        if conn.execute('SELECT has_schema_privilege(%s,%s,%s)',(role,schema,'USAGE WITH GRANT OPTION')).fetchone()!=(False,):
            raise PermissionError('Runtime schema grant options are prohibited')
        if conn.execute('SELECT has_schema_privilege(%s,%s,%s)',(role,schema,'CREATE')).fetchone()!=(False,):
            raise PermissionError('Runtime role cannot create schema objects')
        if conn.execute('''SELECT 1 FROM pg_namespace n WHERE n.nspname=%s AND n.nspowner=%s
            UNION ALL SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relowner=%s LIMIT 1''',(schema,row[0],schema,row[0])).fetchone():
            raise PermissionError('Runtime role cannot own account or retained storage')
    grants=[]
    for schema,table,allowed in policy:
        values=[]
        for privilege in PRIVILEGES:
            actual=conn.execute('SELECT has_table_privilege(%s,%s,%s)',(role,schema+'.'+table,privilege)).fetchone()
            if actual not in ((True,),(False,)):raise ValueError('Missing role privilege result')
            if actual==(True,):
                if privilege not in allowed:raise PermissionError('Unexpected existing runtime grants; no automatic revocation')
                values.append(privilege)
        for privilege in PRIVILEGES:
            if conn.execute('SELECT has_table_privilege(%s,%s,%s)',
                    (role,schema+'.'+table,privilege+' WITH GRANT OPTION')).fetchone()!=(False,):
                raise PermissionError('Runtime grant options are prohibited')
        # Column grants can otherwise bypass table-level denial.
        for privilege in ('SELECT','INSERT','UPDATE','REFERENCES'):
            if conn.execute('SELECT has_any_column_privilege(%s,%s,%s)',
                    (role,schema+'.'+table,privilege+' WITH GRANT OPTION')).fetchone()!=(False,):
                raise PermissionError('Runtime column grant options are prohibited')
            if privilege not in allowed and conn.execute('SELECT has_any_column_privilege(%s,%s,%s)',
                    (role,schema+'.'+table,privilege)).fetchone()!=(False,):
                raise PermissionError('Unexpected column grants; no automatic revocation')
        grants.append((schema,table,tuple(values)))
    return tuple(grants)



def role_catalog_digest(conn):
    # Preserve the version 1 catalog algorithm. Version 2 also pins column ACLs
    # and retained-table grants, which table-only checks cannot fully cover.
    schemas=conn.execute("SELECT nspname,nspacl::text FROM pg_namespace WHERE nspname IN ('public','campaigniq_accounts') ORDER BY 1").fetchall()
    relations=conn.execute("""SELECT n.nspname,c.relname,c.relacl::text,a.attname,a.attacl::text
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
        WHERE n.nspname='campaigniq_accounts' OR (n.nspname='public'
          AND c.relname IN ('artifact_storage_dev','import_drafts','import_draft_documents'))
        ORDER BY 1,2,a.attnum""").fetchall()
    return migration.digest((migration.catalog_digest(conn),schemas,relations))


def review_runtime_grants(conn,role):
    require_transaction(conn);role_name(role)
    migration.verify_schema(conn)
    database=conn.execute('SELECT current_database()').fetchone()[0]
    state=_role_state(conn,role)
    return dict(format='campaigniq.runtime-grants',version=VERSION,database=database,role=role,
        role_oid=conn.execute('SELECT oid FROM pg_roles WHERE rolname=%s',(role,)).fetchone()[0],
        policy_sha256=SOURCE_SHA256,catalog_sha256=role_catalog_digest(conn),
        existing_grants_sha256=migration.digest(state))


def apply_runtime_grants(conn,role,*,reviewed_sha256):
    """Caller owns transaction and must propagate errors; never commits/revokes."""
    from psycopg import sql
    require_transaction(conn);role_name(role)
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('campaigniq_accounts:migrations',0))")
    plan=review_runtime_grants(conn,role)
    if not isinstance(reviewed_sha256,str) or migration.digest(plan)!=reviewed_sha256:
        raise ValueError('Reviewed runtime grant proposal differs')
    rows=conn.execute('SELECT version FROM campaigniq_accounts.schema_migrations ORDER BY version').fetchall()
    if rows in ([(1,),(2,)],[(1,),(2,),(3,)]):
        _verify_complete(conn,role)
        return False
    if rows!=[(1,)]:raise ValueError('Unexpected role migration state')
    for schema in ('public','campaigniq_accounts'):
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
    for schema,table,allowed in POLICY:
        if allowed:
            conn.execute(sql.SQL('GRANT '+','.join(allowed)+' ON TABLE {}.{} TO {}').format(
                sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    _verify_complete(conn,role)
    signature=role_catalog_digest(conn)
    conn.execute('INSERT INTO campaigniq_accounts.schema_migrations(version,source_sha256,catalog_sha256) VALUES(%s,%s,%s)',
        (VERSION,SOURCE_SHA256,signature))
    migration.verify_schema(conn);require_transaction(conn)
    return True


def _verify_complete(conn,role,policy=POLICY):
    state=_role_state(conn,role,policy)
    for actual,(_,_,allowed) in zip(state,policy):
        if set(actual[2])!=set(allowed):raise PermissionError('Runtime grants incomplete')
    for schema in ('public','campaigniq_accounts'):
        if conn.execute('SELECT has_schema_privilege(%s,%s,%s)',(role,schema,'USAGE')).fetchone()!=(True,):
            raise PermissionError('Runtime schema usage missing')
