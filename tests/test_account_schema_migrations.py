from types import SimpleNamespace
from unittest.mock import patch
import pytest
from psycopg.pq import TransactionStatus
from campaigniq import account_schema_migrations as m
from campaigniq.account_schema_rehearsal import restore_conninfo, DATABASE

class Rows:
    def __init__(self,value):self.value=value
    def fetchone(self):return self.value
    def fetchall(self):return self.value

class Connection:
    def __init__(self,*,exists=False,tables=None,ledger=None,status=TransactionStatus.INTRANS):
        self.info=SimpleNamespace(transaction_status=status)
        self.exists=exists;self.tables=tables if tables is not None else m.TABLES
        self.ledger=ledger if ledger is not None else [(m.VERSION,m.SOURCE_SHA256,'a'*64)]
        self.calls=[]
    def execute(self,sql,args=None):
        self.calls.append((sql,args))
        if sql.startswith('SELECT 1 FROM pg_namespace'):return Rows((1,) if self.exists else None)
        if 'SELECT c.relname FROM pg_class' in sql:return Rows([(name,) for name in self.tables])
        if sql.startswith('SELECT version,source_sha256'):return Rows(self.ledger)
        return Rows([])
    def commit(self):raise AssertionError('Migration must not commit')

def test_first_apply_is_qualified_and_never_commits():
    conn=Connection()
    with patch.object(m,'catalog_digest',return_value='a'*64):assert m.apply_migrations(conn) is True
    statements=[sql for sql,_ in conn.calls]
    assert statements[0]=='SET LOCAL search_path=pg_catalog'
    assert any('pg_advisory_xact_lock' in sql for sql in statements)
    ddl=[sql.strip() for sql in statements if sql.lstrip().startswith(('CREATE','REVOKE'))]
    assert len(ddl)>10 and all('campaigniq_accounts' in sql for sql in ddl)
    assert not any('public.' in sql or '_dev.' in sql or 'IF NOT EXISTS' in sql for sql in statements)
    assert conn.calls[-1][1]==(1,m.SOURCE_SHA256,'a'*64)

def test_repeat_validates_without_ddl():
    conn=Connection(exists=True)
    with patch.object(m,'catalog_digest',return_value='a'*64):assert m.apply_migrations(conn) is False
    assert not any(sql.lstrip().startswith(('CREATE','INSERT','ALTER')) for sql,_ in conn.calls)

@pytest.mark.parametrize('ledger',[[],[(2,m.SOURCE_SHA256,'a'*64)],[(1,'0'*64,'a'*64)],[(1,m.SOURCE_SHA256,'b'*64)],[(1,m.SOURCE_SHA256,'a'*64),(2,'0'*64,'a'*64)]])
def test_changed_or_unknown_ledger_never_adopted(ledger):
    conn=Connection(exists=True,ledger=ledger)
    with patch.object(m,'catalog_digest',return_value='a'*64),pytest.raises(ValueError,match='integrity'):m.apply_migrations(conn)
    assert not any(sql.lstrip().startswith(('INSERT','UPDATE','DROP','CREATE')) for sql,_ in conn.calls)

@pytest.mark.parametrize('tables',[set(),m.TABLES-{'audit_events'},m.TABLES|{'foreign_table'}])
def test_untracked_schema_rejected(tables):
    with pytest.raises(ValueError,match='no adoption'):m.apply_migrations(Connection(exists=True,tables=tables))

@pytest.mark.parametrize('status',[TransactionStatus.IDLE,TransactionStatus.INERROR,TransactionStatus.UNKNOWN])
def test_active_transaction_required(status):
    conn=Connection(status=status)
    with pytest.raises(ValueError,match='transaction'):m.apply_migrations(conn)
    assert not conn.calls

def test_catalog_covers_permissions_constraints_indexes_triggers_policies():
    conn=Connection();assert len(m.catalog_digest(conn))==64
    sql=' '.join(query for query,_ in conn.calls)
    for token in ('nspacl','relacl','attnotnull','pg_get_constraintdef','pg_indexes','pg_trigger','pg_policies','pg_proc'):assert token in sql
    assert all(args==(m.SCHEMA,) for _,args in conn.calls)

@pytest.mark.parametrize('key',['RENDER','RENDER_SERVICE_ID','PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGOPTIONS'])
def test_restore_overrides_blocked(monkeypatch,key):
    monkeypatch.setenv(key,'blocked')
    with pytest.raises(ValueError,match='blocked'):restore_conninfo(development_only=True)

def test_restore_target_pinned(monkeypatch):
    from psycopg.conninfo import conninfo_to_dict
    for name in ('RENDER','RENDER_SERVICE_ID','PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGOPTIONS'):monkeypatch.delenv(name,raising=False)
    monkeypatch.setenv('PGDATABASE','production');monkeypatch.setenv('PGHOST','foreign')
    values=conninfo_to_dict(restore_conninfo(development_only=True))
    assert values['dbname']==DATABASE and values['host']=='/var/run/postgresql' and values['port']=='5432'
    with pytest.raises(ValueError):restore_conninfo(development_only=False)
