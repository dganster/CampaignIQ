"""Reviewed separate personal preference role; no financial or original writes."""
from campaigniq import account_schema_migrations as m
from campaigniq.account_runtime_roles import POLICY as INPUT_POLICY,role_name,_role_state,_verify_complete,role_catalog_digest
from campaigniq.persistence.deployment_account_directory import require_transaction

POLICY=tuple((schema,table,('SELECT','INSERT','UPDATE') if table=='account_preferences'
    else ('SELECT',) if table in ('import_drafts','import_draft_documents') else allowed)
    for schema,table,allowed in INPUT_POLICY)
SOURCE_SHA256=m.digest(dict(version=4,policy=POLICY,protocol='separate personal preference role; exact reviewed OID; no financial writes; no old preference copy'))


def review_preference_grants(conn,role):
    require_transaction(conn);role_name(role);m.verify_schema(conn)
    rows=conn.execute('SELECT version FROM campaigniq_accounts.schema_migrations ORDER BY version').fetchall()
    if rows not in ([(1,),(2,),(3,)],[(1,),(2,),(3,),(4,)]):raise ValueError('Reviewed input and publication migrations required first')
    state=_role_state(conn,role,POLICY)
    return dict(format='campaigniq.preference-grants',version=4,role=role,
        role_oid=conn.execute('SELECT oid FROM pg_roles WHERE rolname=%s',(role,)).fetchone()[0],
        database=conn.execute('SELECT current_database()').fetchone()[0],policy_sha256=SOURCE_SHA256,
        catalog_sha256=role_catalog_digest(conn),existing_grants_sha256=m.digest(state))


def apply_preference_grants(conn,role,*,reviewed_sha256):
    from psycopg import sql
    require_transaction(conn);role_name(role)
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('campaigniq_accounts:migrations',0))")
    plan=review_preference_grants(conn,role)
    if m.digest(plan)!=reviewed_sha256:raise ValueError('Reviewed preference grant proposal differs')
    rows=conn.execute('SELECT version FROM campaigniq_accounts.schema_migrations ORDER BY version').fetchall()
    if rows==[(1,),(2,),(3,),(4,)]:
        _verify_complete(conn,role,POLICY);return False
    for schema in ('public','campaigniq_accounts'):
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
    for schema,table,allowed in POLICY:
        if allowed:conn.execute(sql.SQL('GRANT '+','.join(allowed)+' ON TABLE {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    _verify_complete(conn,role,POLICY)
    conn.execute('INSERT INTO campaigniq_accounts.schema_migrations(version,source_sha256,catalog_sha256) VALUES(%s,%s,%s)',(4,SOURCE_SHA256,role_catalog_digest(conn)))
    m.verify_schema(conn);require_transaction(conn);return True


def validate_preference_role(conn):
    _verify_complete(conn,conn.execute('SELECT current_user').fetchone()[0],POLICY)
