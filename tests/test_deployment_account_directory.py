from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
import pytest
from psycopg.pq import TransactionStatus
from campaigniq.account_administration import Identity,LegacyOwner,Permission,propose_legacy_directory
from campaigniq.account_backend_config import AccountDatabaseTarget,AccountDirectorySettings
from campaigniq.account_directory_bootstrap import review_bootstrap,bootstrap_directory
from campaigniq.account_backend_rehearsal import ServerSession
from campaigniq.persistence.account_directory_codec import encode,checksum
from campaigniq.persistence.deployment_account_directory import DirectoryTransaction,PostgresAccountDirectory,validate_runtime_role

ISSUER='https://accounts.example.invalid'
A=Identity(ISSUER,'owner-a');B=Identity(ISSUER,'owner-b')
OWNERS=(LegacyOwner(A,'workspace-a','Owner A','Account A','schwab'),LegacyOwner(B,'workspace-b','Owner B','Account B','schwab'))

def settings():return AccountDirectorySettings(str(uuid4()),ISSUER,(('owner-a','workspace-a'),('owner-b','workspace-b')))
def target():return AccountDatabaseTarget('db.example.invalid','accounts','account_runtime')
def dsn():return 'host=db.example.invalid port=5432 dbname=accounts user=account_runtime sslmode=verify-full password=private-marker'

class Result:
    def __init__(self,rows=None,row=None,rowcount=1):self.rows=rows or [];self.row=row;self.rowcount=rowcount
    def fetchone(self):return self.row
    def fetchall(self):return self.rows

class Connection:
    def __init__(self,config,initial=None):
        self.info=SimpleNamespace(transaction_status=TransactionStatus.INTRANS)
        self.config=config;self.document=encode(initial) if initial else None
        self.saved_hash=checksum(self.document) if initial else None
        self.audit=[];self.calls=[];self.exit_error=None
    def __enter__(self):return self
    def __exit__(self,kind,value,tb):self.exit_error=kind
    def execute(self,statement,args=None):
        text=str(statement);self.calls.append((text,args))
        if 'SELECT registry_id::text' in text:return Result(rows=[(self.config.registry_id,)] if self.document else [])
        if 'SELECT revision,document,document_sha256' in text:
            return Result(row=(len(self.audit),deepcopy(self.document),self.saved_hash) if self.document else None)
        if 'SELECT sequence,event,event_sha256' in text:return Result(rows=deepcopy(self.audit))
        if text.startswith('INSERT INTO campaigniq_accounts.directories'):
            if self.document:return Result(rowcount=0)
            self.document=deepcopy(args[1].obj);self.saved_hash=args[2];return Result()
        if text.startswith('UPDATE campaigniq_accounts.directories'):
            if args[-1]!=len(self.audit):return Result(rowcount=0)
            self.document=deepcopy(args[1].obj);self.saved_hash=args[2];return Result()
        if text.startswith('INSERT INTO campaigniq_accounts.audit_events'):
            self.audit.append((args[1],deepcopy(args[2].obj),args[3]));return Result()
        return Result()

@pytest.fixture
def setup():
    config=settings();conn=Connection(config,propose_legacy_directory(OWNERS,A))
    return config,conn,DirectoryTransaction(conn,config),ServerSession(A),ServerSession(B)


def test_current_server_actor_atomic_cas_and_owner_permissions(setup):
    config,conn,tx,sa,sb=setup
    initial=tx.snapshot(sa);a,b=initial.accounts
    assert tx.authorize(sa,a.id).storage_scope=='workspace-a'
    assert tx.authorize(sb,b.id).storage_scope=='workspace-b'
    with pytest.raises(PermissionError):tx.authorize(sa,b.id)
    with pytest.raises(PermissionError):tx.snapshot(sb)
    assert tx.execute(sa,0,'configure_account',account_id=a.id,name='Renamed')==1
    assert tx.snapshot(sa).events[-1].actor_id==initial.users[0].id
    assert any('FOR UPDATE' in query for query,_ in conn.calls)
    before=deepcopy((conn.document,conn.audit))
    with pytest.raises(ValueError):tx.execute(sa,0,'configure_account',account_id=a.id,name='Stale')
    with pytest.raises(PermissionError):tx.execute(sb,1,'configure_account',account_id=b.id,name='Forbidden')
    assert (conn.document,conn.audit)==before


def test_grants_revocation_disabled_and_last_admin_use_existing_policy(setup):
    _,_,tx,sa,sb=setup
    directory=tx.snapshot(sa);a,b=directory.accounts;member=directory.users[1].id
    def command(action,**kw):return tx.execute(sa,tx.snapshot(sa).revision,action,**kw)
    command('set_grant',user_id=member,account_id=a.id,permission=Permission.VIEW)
    assert tx.authorize(sb,a.id).writable is False
    with pytest.raises(PermissionError):tx.authorize(sb,a.id,write=True)
    command('set_grant',user_id=member,account_id=a.id,permission=None)
    with pytest.raises(PermissionError):tx.authorize(sb,a.id)
    command('configure_account',account_id=b.id,archived=True)
    assert not tx.authorize(sb,b.id).writable
    command('configure_user',user_id=member,enabled=False)
    with pytest.raises(PermissionError):tx.session(sb)
    with pytest.raises(ValueError):command('configure_user',user_id=directory.users[0].id,admin=False)


@pytest.mark.parametrize('session',[{},A,ServerSession(A,logged_in=False),ServerSession(Identity('https://foreign.invalid','owner-a'))])
def test_nonserver_logged_out_and_wrong_issuer_sessions_rejected(setup,session):
    _,conn,tx,_,_=setup;before=deepcopy((conn.document,conn.audit))
    with pytest.raises(PermissionError):tx.session(session)
    assert (conn.document,conn.audit)==before


def test_corrupt_directory_and_audit_fail_closed(setup):
    _,conn,tx,sa,_=setup
    original=conn.saved_hash;conn.saved_hash='0'*64
    with pytest.raises(ValueError,match='checksum'):tx.session(sa)
    conn.saved_hash=original
    tx.execute(sa,0,'configure_account',account_id=tx.snapshot(sa).accounts[0].id,name='Renamed')
    seq,event,_=conn.audit[0];conn.audit[0]=(seq,event,'0'*64)
    with pytest.raises(ValueError,match='audit'):tx.session(sa)


def test_changed_mapping_and_expired_handle_rejected(setup):
    config,conn,tx,sa,_=setup
    changed=AccountDirectorySettings(config.registry_id,ISSUER,(('owner-a','workspace-b'),('owner-b','workspace-a')))
    with pytest.raises(ValueError):DirectoryTransaction(conn,changed).session(sa)
    conn.info.transaction_status=TransactionStatus.IDLE
    with pytest.raises(ValueError,match='expired'):tx.session(sa)


def test_wrong_issuer_registration_or_forged_actor_argument_never_writes(setup):
    _,conn,tx,sa,_=setup
    for command,args in [('add_user',dict(identity=Identity('https://foreign.invalid','new'),name='Foreign')),
                         ('configure_user',dict(actor_identity=A))]:
        with pytest.raises(ValueError):tx.execute(sa,0,command,**args)
    assert conn.audit==[]


def test_bootstrap_is_explicit_deterministic_idempotent_and_review_bound():
    config=settings();conn=Connection(config)
    with patch('campaigniq.account_directory_bootstrap.legacy_fingerprint',return_value='a'*64),patch('campaigniq.account_directory_bootstrap.verify_schema'):
        plan=review_bootstrap(conn,config,OWNERS,A)
        assert review_bootstrap(conn,config,tuple(reversed(OWNERS)),A)==plan
        assert conn.document is None
        with pytest.raises(ValueError):bootstrap_directory(conn,config,plan,reviewed_sha256='0'*64)
        assert bootstrap_directory(conn,config,plan,reviewed_sha256=plan.sha256) is True
        assert bootstrap_directory(conn,config,plan,reviewed_sha256=plan.sha256) is False
        assert conn.document==encode(plan.directory) and conn.audit==[]
        conn.document['users'][0]['name']='Different';conn.saved_hash=checksum(conn.document)
        with pytest.raises(ValueError,match='cannot overwrite'):bootstrap_directory(conn,config,plan,reviewed_sha256=plan.sha256)


def test_changed_source_or_registry_blocks_bootstrap_without_write():
    config=settings();conn=Connection(config)
    with patch('campaigniq.account_directory_bootstrap.legacy_fingerprint',return_value='a'*64):plan=review_bootstrap(conn,config,OWNERS,A)
    with patch('campaigniq.account_directory_bootstrap.legacy_fingerprint',return_value='b'*64):
        with pytest.raises(ValueError):bootstrap_directory(conn,config,plan,reviewed_sha256=plan.sha256)
    with pytest.raises(ValueError):bootstrap_directory(conn,settings(),plan,reviewed_sha256=plan.sha256)
    assert conn.document is None
    conn.info.transaction_status=TransactionStatus.IDLE
    with pytest.raises(ValueError,match='Active transaction'):bootstrap_directory(conn,config,plan,reviewed_sha256=plan.sha256)


@pytest.mark.parametrize('pairs',[(),(('owner-a','workspace-a'),('owner-a','workspace-b')),(('owner-a','workspace-a'),('owner-b','workspace-a')),(('owner-a','../workspace-a'),),(('owner-b','workspace-b'),('owner-a','workspace-a'))])
def test_ambiguous_or_noncanonical_settings_rejected(pairs):
    with pytest.raises(ValueError):AccountDirectorySettings(str(uuid4()),ISSUER,pairs)


@pytest.mark.parametrize('host',['localhost','127.0.0.1','0.0.0.0','::1','/var/run/postgresql','db1,db2','db@foreign','x.localhost'])
def test_runtime_target_cannot_be_local_or_ambiguous(host):
    with pytest.raises(ValueError):AccountDatabaseTarget(host,'accounts','runtime')


@pytest.mark.parametrize('change',['host=foreign.invalid','dbname=other','user=owner','sslmode=require','hostaddr=127.0.0.1','options=-csearch_path=public','service=foreign'])
def test_dsn_endpoint_role_tls_and_overrides_pinned(change):
    with pytest.raises(ValueError):target().conninfo(dsn()+' '+change)


@pytest.mark.parametrize('key',['PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGOPTIONS'])
def test_ambient_routing_overrides_rejected(monkeypatch,key):
    monkeypatch.setenv(key,'foreign')
    with pytest.raises(ValueError):target().conninfo(dsn())


def test_runtime_constructor_is_offline_and_hides_dsn():
    with patch('psycopg.connect',side_effect=AssertionError('No startup connection')):
        store=PostgresAccountDirectory(dsn(),settings(),target())
    assert 'private-marker' not in repr(store)
    assert not hasattr(store,'initialize') and not hasattr(store,'bootstrap')


@pytest.mark.parametrize('field',range(3,11))
def test_runtime_privileged_role_refused(field):
    values=['accounts','account_runtime','account_runtime']+[False]*8;values[field]=True
    conn=SimpleNamespace(execute=lambda *a:Result(row=tuple(values)))
    with pytest.raises(PermissionError):validate_runtime_role(conn,target())


def test_runtime_connection_readonly_no_ddl_and_caught_sql_error_cannot_commit(setup):
    config,conn,_,sa,_=setup
    store=PostgresAccountDirectory(dsn(),config,target())
    with patch('psycopg.connect',return_value=conn),patch('campaigniq.persistence.deployment_account_directory.validate_runtime_role'),patch('campaigniq.persistence.deployment_account_directory.verify_schema'):
        assert store.session(sa).identity==A
        assert any('REPEATABLE READ READ ONLY' in q for q,_ in conn.calls)
        assert not any(q.startswith(('CREATE','INSERT','UPDATE')) for q,_ in conn.calls)
        with pytest.raises(RuntimeError,match='commit blocked'):
            with store._connection(readonly=False):conn.info.transaction_status=TransactionStatus.INERROR
        assert conn.exit_error is RuntimeError


def test_exact_runtime_metadata_grants_required():
    class RoleConnection:
        bad=False
        def execute(self,query,args=None):
            if args is None:return Result(row=('accounts','account_runtime','account_runtime')+(False,)*8)
            table,privilege=args
            allowed={'directories':{'SELECT','UPDATE'},'audit_events':{'SELECT','INSERT'},'schema_migrations':{'SELECT'}}
            return Result(row=(True if self.bad else privilege in allowed[table.split('.')[1]],))
    conn=RoleConnection();validate_runtime_role(conn,target())
    conn.bad=True
    with pytest.raises(PermissionError,match='grants differ'):validate_runtime_role(conn,target())


def test_source_change_after_review_before_locked_insert_blocks_bootstrap():
    config=settings();conn=Connection(config)
    with patch('campaigniq.account_directory_bootstrap.legacy_fingerprint',return_value='a'*64):
        plan=review_bootstrap(conn,config,OWNERS,A)
    with patch('campaigniq.account_directory_bootstrap.legacy_fingerprint',side_effect=['a'*64,'b'*64]),patch('campaigniq.account_directory_bootstrap.verify_schema'):
        with pytest.raises(ValueError,match='changed since'):bootstrap_directory(conn,config,plan,reviewed_sha256=plan.sha256)
    assert conn.document is None


def test_second_registry_cannot_be_bootstrapped():
    config=settings();conn=Connection(config)
    with patch('campaigniq.account_directory_bootstrap.legacy_fingerprint',return_value='a'*64),patch('campaigniq.account_directory_bootstrap.verify_schema'):
        plan=review_bootstrap(conn,config,OWNERS,A)
        original=conn.execute
        def execute(query,args=None):
            if 'SELECT registry_id::text' in str(query):return Result(rows=[(str(uuid4()),)])
            return original(query,args)
        conn.execute=execute
        with pytest.raises(ValueError,match='registry differs'):bootstrap_directory(conn,config,plan,reviewed_sha256=plan.sha256)
    assert conn.document is None


def test_bootstrap_namespace_inventory_is_complete_and_rows_are_readonly():
    from campaigniq.account_directory_bootstrap import legacy_fingerprint
    class SourceConnection:
        info=SimpleNamespace(transaction_status=TransactionStatus.INTRANS)
        observed=[('workspace-a',),('workspace-b',)]
        def __init__(self):self.calls=[]
        def execute(self,query,args=None):
            self.calls.append(str(query))
            return Result(rows=self.observed if 'SELECT namespace FROM public.' in str(query) else [('rowdigest',2)])
    conn=SourceConnection();assert len(legacy_fingerprint(conn,settings()))==64
    assert not any(query.startswith(('INSERT','UPDATE','DELETE','CREATE')) for query in conn.calls)
    conn.observed.append(('workspace-unmapped',))
    with pytest.raises(ValueError,match='mapping differs'):legacy_fingerprint(conn,settings())
