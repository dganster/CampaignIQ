"""Frozen pre-batching validator for parity tests only."""
from campaigniq.account_runtime_roles import role_name,POLICY,PRIVILEGES

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
