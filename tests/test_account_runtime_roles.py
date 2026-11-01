from types import SimpleNamespace
from unittest.mock import patch
import pytest
from psycopg.pq import TransactionStatus
from campaigniq import account_runtime_roles as r
from campaigniq import account_schema_migrations as m
from tests.test_account_schema_migrations import Connection as SchemaConnection

class Result:
    def __init__(self,value):self.value=value
    def fetchone(self):return self.value
    def fetchall(self):return self.value

class Connection:
    def __init__(self):
        self.info=SimpleNamespace(transaction_status=TransactionStatus.INTRANS)
        self.grants={};self.calls=[];self.ledger=[(1,)];self.member=False;self.elevated=False
        self.owner=False;self.create=False;self.column=False;self.option=False;self.oid=123
    def execute(self,query,args=None):
        from psycopg import sql
        if isinstance(query,sql.Composable):query=query.as_string()
        self.calls.append((query,args))
        if '/* campaigniq_batched_privileges */' in query:
            tables,privileges=args[-2:]
            rows=[]
            for number,table in enumerate(tables,1):
                for privilege in privileges:
                    column=self.column if privilege in ('SELECT','INSERT','UPDATE','REFERENCES') else False
                    rows.append((number,privilege,privilege in self.grants.get(table,()),self.option,column,column))
            return Result(rows)
        if 'SELECT oid,rolsuper' in query:return Result((self.oid,self.elevated,False,False,False,False))
        if 'SELECT oid FROM pg_roles' in query:return Result((self.oid,))
        if 'FROM pg_auth_members' in query:return Result((1,) if self.member else None)
        if 'has_database_privilege' in query:return Result((self.create,))
        if 'has_schema_privilege' in query:return Result((True if args[-1]=='USAGE' else self.create,))
        if 'n.nspowner=%s' in query:return Result((1,) if self.owner else None)
        if 'has_table_privilege' in query:
            if args[-1].endswith(' WITH GRANT OPTION'):return Result((self.option,))
            return Result((args[-1] in self.grants.get(args[1],()),))
        if 'has_any_column_privilege' in query:return Result((self.column,))
        if query=='SELECT current_database()':return Result(('local_restore',))
        if query.startswith('SELECT version FROM'):return Result(list(self.ledger))
        if query.startswith('GRANT ') and ' ON TABLE ' in query:
            parts=query.split();self.grants[parts[4].replace('"','')]=tuple(parts[1].split(','))
        if query.startswith('INSERT INTO campaigniq_accounts.schema_migrations'):self.ledger.append((2,))
        return Result(None)
    def commit(self):raise AssertionError('Caller owns transaction')

@pytest.fixture
def conn():
    with patch.object(m,'verify_schema'),patch.object(m,'catalog_digest',return_value='a'*64),patch.object(r,'role_catalog_digest',return_value='a'*64):yield Connection()

@pytest.mark.parametrize('name',['',None,'PUBLIC','public','pg_test','Role','role;DROP','a'*64,'space role'])
def test_invalid_role_names_rejected_before_queries(conn,name):
    with pytest.raises(ValueError):r.review_runtime_grants(conn,name)
    assert not conn.calls

@pytest.mark.parametrize('status',[TransactionStatus.IDLE,TransactionStatus.INERROR,TransactionStatus.UNKNOWN])
def test_role_grants_require_live_caller_transaction(conn,status):
    conn.info.transaction_status=status
    with pytest.raises(ValueError):r.apply_runtime_grants(conn,'runtime',reviewed_sha256='a'*64)
    assert not conn.calls

@pytest.mark.parametrize('attribute',['member','elevated','owner','create','column','option'])
def test_unsafe_role_or_effective_grants_rejected_without_repair(conn,attribute):
    setattr(conn,attribute,True)
    with pytest.raises(PermissionError):r.review_runtime_grants(conn,'runtime')
    assert not any(q.startswith(('GRANT','REVOKE','ALTER','INSERT')) for q,_ in conn.calls)


def test_extra_artifact_write_cannot_be_silently_revoked(conn):
    conn.grants['campaigniq_accounts.artifact_storage_dev']=('INSERT',)
    with pytest.raises(PermissionError,match='Unexpected existing'):r.review_runtime_grants(conn,'runtime')
    assert not any(q.startswith(('REVOKE','GRANT')) for q,_ in conn.calls)


def test_exact_review_and_role_oid_are_required_before_grants(conn):
    plan=r.review_runtime_grants(conn,'runtime');assert plan==r.review_runtime_grants(conn,'runtime')
    checksum=m.digest(plan);conn.oid+=1
    with pytest.raises(ValueError,match='proposal differs'):r.apply_runtime_grants(conn,'runtime',reviewed_sha256=checksum)
    assert not any(q.startswith('GRANT') for q,_ in conn.calls)


def test_grants_match_existing_input_and_metadata_policy_and_repeat_is_noop(conn):
    plan=r.review_runtime_grants(conn,'runtime')
    assert r.apply_runtime_grants(conn,'runtime',reviewed_sha256=m.digest(plan)) is True
    for schema,table,allowed in r.POLICY:assert set(conn.grants.get(schema+'.'+table,()))==set(allowed)
    assert all('public.' not in q.replace('"','') for q,_ in conn.calls if q.startswith('GRANT') and any(x in q for x in ('INSERT','UPDATE')))
    assert not any(q.startswith(('CREATE','ALTER','REVOKE','DROP','COMMIT')) for q,_ in conn.calls)
    again=r.review_runtime_grants(conn,'runtime');conn.calls.clear()
    assert r.apply_runtime_grants(conn,'runtime',reviewed_sha256=m.digest(again)) is False
    assert not any(q.startswith(('GRANT','INSERT','UPDATE')) for q,_ in conn.calls)


def test_version_one_source_is_retained_and_version_two_integrity_checked():
    old=(1,m.SOURCE_SHA256,'b'*64);new=(2,r.SOURCE_SHA256,'a'*64)
    conn=SchemaConnection(exists=True,ledger=[old,new])
    with patch.object(m,'catalog_digest',return_value='a'*64),patch.object(r,'role_catalog_digest',return_value='a'*64):
        m.verify_schema(conn)
        assert m.apply_migrations(conn) is False
    assert not any(q.startswith(('INSERT','UPDATE','CREATE','GRANT')) for q,_ in conn.calls)
    for ledger in ([old,(2,'0'*64,'a'*64)],[old,(2,r.SOURCE_SHA256,'c'*64)],[old,new,(3,'d'*64,'a'*64)]):
        with patch.object(m,'catalog_digest',return_value='a'*64),patch.object(r,'role_catalog_digest',return_value='a'*64),pytest.raises(ValueError,match='integrity'):
            m.verify_schema(SchemaConnection(exists=True,ledger=ledger))


def test_version_two_catalog_covers_column_and_retained_table_acls():
    class Catalog:
        def __init__(self):self.calls=[];self.column='original'
        def execute(self,query,args=None):
            self.calls.append(query)
            return Result([(self.column,)]) if 'attacl' in query else Result([])
    conn=Catalog()
    with patch.object(m,'catalog_digest',return_value='a'*64):
        before=r.role_catalog_digest(conn);conn.column='unexpected update grant'
        assert r.role_catalog_digest(conn)!=before
    assert any('attacl' in q and "n.nspname='public'" in q for q in conn.calls)
