"""Separate reviewed publication role, without changing ordinary input permissions."""
from campaigniq import account_schema_migrations as m
from campaigniq.account_runtime_roles import POLICY as INPUT_POLICY,role_name,_role_state,_verify_complete,role_catalog_digest
from campaigniq.persistence.deployment_account_directory import require_transaction

POLICY=tuple((schema,table,('SELECT','INSERT') if schema=='campaigniq_accounts' and table=='artifact_storage_dev'
    else ('SELECT','UPDATE') if schema=='campaigniq_accounts' and table=='import_drafts'
    else ('SELECT',) if schema=='campaigniq_accounts' and table=='import_draft_documents'
    else allowed) for schema,table,allowed in INPUT_POLICY)
SOURCE_SHA256=m.digest(dict(version=3,policy=POLICY,protocol='explicit role OID and catalog review; no input-role changes; no legacy writes; no default privileges'))


def review_publication_grants(conn,role):
    require_transaction(conn);role_name(role);m.verify_schema(conn)
    rows=conn.execute('SELECT version FROM campaigniq_accounts.schema_migrations ORDER BY version').fetchall()
    if rows not in ([(1,),(2,)],[(1,),(2,),(3,)],[(1,),(2,),(3,),(4,)]):raise ValueError('Reviewed input-role migration required first')
    state=_role_state(conn,role,POLICY)
    return dict(format='campaigniq.publication-grants',version=3,role=role,
        role_oid=conn.execute('SELECT oid FROM pg_roles WHERE rolname=%s',(role,)).fetchone()[0],
        database=conn.execute('SELECT current_database()').fetchone()[0],policy_sha256=SOURCE_SHA256,
        catalog_sha256=role_catalog_digest(conn),existing_grants_sha256=m.digest(state))


def apply_publication_grants(conn,role,*,reviewed_sha256):
    from psycopg import sql
    require_transaction(conn);role_name(role)
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('campaigniq_accounts:migrations',0))")
    plan=review_publication_grants(conn,role)
    if m.digest(plan)!=reviewed_sha256:raise ValueError('Reviewed publication grant proposal differs')
    rows=conn.execute('SELECT version FROM campaigniq_accounts.schema_migrations ORDER BY version').fetchall()
    if rows in ([(1,),(2,),(3,)],[(1,),(2,),(3,),(4,)]):
        _verify_complete(conn,role,POLICY);return False
    for schema in ('public','campaigniq_accounts'):
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
    for schema,table,allowed in POLICY:
        if allowed:conn.execute(sql.SQL('GRANT '+','.join(allowed)+' ON TABLE {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    _verify_complete(conn,role,POLICY)
    conn.execute('INSERT INTO campaigniq_accounts.schema_migrations(version,source_sha256,catalog_sha256) VALUES(%s,%s,%s)',(3,SOURCE_SHA256,role_catalog_digest(conn)))
    m.verify_schema(conn);require_transaction(conn);return True


def validate_publication_role(conn):
    row=conn.execute('SELECT current_user').fetchone()
    _verify_complete(conn,row[0],POLICY)
