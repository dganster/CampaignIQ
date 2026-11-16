"""Read all effective table/column privileges once in the caller's snapshot."""

def read_role_guards(conn,role):
    """Fresh bounded catalog read; no connection or cross-request cache."""
    rows=conn.execute('''/* campaigniq_batched_role_guards */
        SELECT s.ord,s.schema_name,r.rolsuper,r.rolcreatedb,r.rolcreaterole,
            r.rolreplication,r.rolbypassrls,
            EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid),
            has_database_privilege(r.rolname,current_database(),'CREATE'),
            has_schema_privilege(r.rolname,s.schema_name,'USAGE WITH GRANT OPTION'),
            has_schema_privilege(r.rolname,s.schema_name,'CREATE'),
            EXISTS(SELECT 1 FROM pg_namespace n
                WHERE n.nspname=s.schema_name AND n.nspowner=r.oid),
            EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=s.schema_name AND c.relowner=r.oid)
        FROM pg_roles r CROSS JOIN
            unnest(%s::text[]) WITH ORDINALITY AS s(schema_name,ord)
        WHERE r.rolname=%s ORDER BY s.ord''',
        (['public','campaigniq_accounts'],role)).fetchall()
    if len(rows)!=2:raise PermissionError('Existing unprivileged runtime role required')
    for number,schema in enumerate(('public','campaigniq_accounts'),1):
        row=rows[number-1]
        if (len(row)!=13 or type(row[0]) is not int or row[:2]!=(number,schema)
            or any(type(value) is not bool for value in row[2:])):
            raise ValueError('Missing role guard result')
        if any(row[2:7]):raise PermissionError('Existing unprivileged runtime role required')
        if row[7]:raise PermissionError('Runtime role memberships are prohibited')
        if row[8]:raise PermissionError('Runtime role cannot create database objects')
        if row[9]:raise PermissionError('Runtime schema grant options are prohibited')
        if row[10]:raise PermissionError('Runtime role cannot create schema objects')
        if any(row[11:]):raise PermissionError('Runtime role cannot own account or retained storage')

def read_privileges(conn,role,policy,privileges):
    rows=conn.execute('''/* campaigniq_batched_privileges */
        SELECT t.ord,p.privilege,
            has_table_privilege(%s,t.table_name,p.privilege),
            has_table_privilege(%s,t.table_name,p.privilege||' WITH GRANT OPTION'),
            CASE WHEN p.privilege IN ('SELECT','INSERT','UPDATE','REFERENCES')
                THEN has_any_column_privilege(%s,t.table_name,p.privilege) ELSE false END,
            CASE WHEN p.privilege IN ('SELECT','INSERT','UPDATE','REFERENCES')
                THEN has_any_column_privilege(%s,t.table_name,p.privilege||' WITH GRANT OPTION') ELSE false END
        FROM unnest(%s::text[]) WITH ORDINALITY AS t(table_name,ord)
        CROSS JOIN unnest(%s::text[]) WITH ORDINALITY AS p(privilege,ord)
        ORDER BY t.ord,p.ord''',
        (role,role,role,role,[schema+'.'+table for schema,table,_ in policy],list(privileges))).fetchall()
    if len(rows)!=len(policy)*len(privileges):raise ValueError('Missing role privilege result')
    grants=[];offset=0
    for number,(schema,table,allowed) in enumerate(policy,1):
        values=[]
        for privilege in privileges:
            row=rows[offset];offset+=1
            if (len(row)!=6 or type(row[0]) is not int or row[:2]!=(number,privilege)
                or any(type(value) is not bool for value in row[2:])):raise ValueError('Missing role privilege result')
            actual,option,column,column_option=row[2:]
            if actual:
                if privilege not in allowed:raise PermissionError('Unexpected existing runtime grants; no automatic revocation')
                values.append(privilege)
            if option:raise PermissionError('Runtime grant options are prohibited')
            if column_option:raise PermissionError('Runtime column grant options are prohibited')
            if privilege not in allowed and column:raise PermissionError('Unexpected column grants; no automatic revocation')
        grants.append((schema,table,tuple(values)))
    return tuple(grants)
