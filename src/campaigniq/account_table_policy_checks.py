"""Equivalent table-policy checks in one query, always in the current transaction."""
PRIVILEGES=('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER')
def validate_tables(conn,policy,*,check_ownership=False,grants_error='Account runtime metadata grants differ',owner_error='Account input role cannot own storage tables'):
    rows=conn.execute('''/* campaigniq_current_table_policy */
        SELECT t.ord,p.privilege,
               has_table_privilege(current_user,t.table_name,p.privilege),
               CASE WHEN %s THEN pg_has_role(current_user,c.relowner,'MEMBER') ELSE false END
        FROM unnest(%s::text[]) WITH ORDINALITY t(table_name,ord)
        CROSS JOIN unnest(%s::text[]) WITH ORDINALITY p(privilege,ord)
        JOIN pg_class c ON c.oid=to_regclass(t.table_name)
        ORDER BY t.ord,p.ord''',
        (check_ownership,[schema+'.'+table for schema,table,_ in policy],list(PRIVILEGES))).fetchall()
    if len(rows)!=len(policy)*len(PRIVILEGES):raise PermissionError(grants_error)
    offset=0
    for ordinal,(_,_,allowed) in enumerate(policy,1):
        for privilege in PRIVILEGES:
            row=rows[offset];offset+=1
            if (len(row)!=4 or type(row[0]) is not int or row[:2]!=(ordinal,privilege) or
                type(row[2]) is not bool or type(row[3]) is not bool or row[2]!=(privilege in allowed)):
                raise PermissionError(grants_error)
            if check_ownership and row[3]:raise PermissionError(owner_error)
