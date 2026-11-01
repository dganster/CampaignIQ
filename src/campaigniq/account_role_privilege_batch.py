"""Read all effective table/column privileges once in the caller's snapshot."""

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
