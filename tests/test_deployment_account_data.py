from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
import pytest
from psycopg.pq import TransactionStatus
from tests.test_deployment_account_directory import Connection,Result,settings,target,dsn,setup,A,B,ServerSession
from campaigniq.account_administration import Permission
from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
from campaigniq.account_deployment_legacy import DeploymentLegacyAccountAccess
from campaigniq.account_selection_rehearsal import synthetic_sources
from campaigniq.persistence.account_data_session import ClosedAccountSession,AccountTransactionFailed
from campaigniq.persistence.deployment_account_directory import PostgresAccountDirectory
from campaigniq.persistence.deployment_account_data import PostgresAccountDataGateway,_open_session,validate_data_role


@pytest.fixture
def data(setup,monkeypatch):
    config,base,tx,sa,sb=setup
    original=tx.snapshot(sa);owner=original.users[0].id
    tx.execute(sa,0,'add_account',owner_id=owner,name='New A',broker='schwab')
    tx.execute(sa,1,'add_account',owner_id=owner,name='New B',broker='schwab')
    a,b=(item.id for item in tx.snapshot(sa).accounts if not item.legacy)
    store=PostgresAccountDirectory(dsn(),config,target());gateway=PostgresAccountDataGateway(store)
    events=[];connections=[]
    def route(conn):
        execute=conn.execute
        conn.schema='campaigniq_accounts'
        def run(query,args=None):
            if query.startswith('SET LOCAL search_path='):
                conn.schema='public' if 'pg_catalog,public' in query else 'campaigniq_accounts'
            if 'SELECT n.nspname,c.relname' in query:
                return Result(rows=[(conn.schema,n) for n in ('artifact_storage_dev','import_draft_documents','import_drafts')])
            return execute(query,args)
        conn.execute=run
        return conn
    route(base)
    @contextmanager
    def factory(*,readonly):
        conn=route(Connection(config)) if readonly else base
        if readonly:
            conn.document=deepcopy(base.document);conn.saved_hash=base.saved_hash;conn.audit=deepcopy(base.audit)
        connections.append(conn);events.append(('enter',readonly))
        try:yield conn
        finally:events.append(('exit',readonly))
    monkeypatch.setattr(gateway,'_connection',factory)
    return SimpleNamespace(config=config,base=base,tx=tx,sa=sa,sb=sb,a=a,b=b,
                           legacy=original.accounts[0].id,gateway=gateway,events=events,connections=connections)


def test_new_and_legacy_routing_use_exact_persisted_scope_and_no_fallback(data):
    with data.gateway.transaction(data.sa,data.a) as session:
        assert session.drafts.workspace==data.tx.authorize(data.sa,data.a).storage_scope
        assert session._connection.schema=='campaigniq_accounts'
    assert data.events==[('enter',False),('enter',True),('exit',True),('exit',False)]
    with data.gateway.legacy_transaction(data.sa,data.legacy) as (conn,session,revision):
        assert conn.schema=='public' and session.drafts.workspace=='workspace-a'
        assert revision==data.tx.snapshot(data.sa).revision
    with pytest.raises(ValueError):
        with data.gateway.transaction(data.sa,data.legacy):pass
    with pytest.raises(ValueError):
        with data.gateway.legacy_transaction(data.sa,data.a):pass


def test_readonly_and_upload_sessions_never_offer_artifact_writes_and_handles_expire(data):
    for write in (False,True):
        with data.gateway.transaction(data.sa,data.a,write=write) as session:
            with pytest.raises(PermissionError):session.artifacts.write_text('blocked.json','{}')
            with pytest.raises(PermissionError):session.artifacts.delete('blocked.json')
            with pytest.raises(PermissionError):session.artifacts._storage.initialize()
        with pytest.raises(ClosedAccountSession):session.drafts.list_drafts()
    with data.gateway.transaction(data.sa,data.a) as session:
        with pytest.raises(PermissionError):session.drafts.create_or_resume(2025,1)


def test_foreign_logged_out_view_revoked_archive_and_disable_rechecked(data):
    for user in (data.sb,ServerSession(A,logged_in=False),A,{}):
        with pytest.raises(PermissionError):
            with data.gateway.transaction(user,data.a):pass
    member=data.tx.snapshot(data.sa).user(B).id
    def command(action,**args):data.tx.execute(data.sa,data.tx.snapshot(data.sa).revision,action,**args)
    command('set_grant',user_id=member,account_id=data.a,permission=Permission.VIEW)
    with data.gateway.transaction(data.sb,data.a):pass
    with pytest.raises(PermissionError):
        with data.gateway.transaction(data.sb,data.a,write=True):pass
    command('set_grant',user_id=member,account_id=data.a,permission=None)
    with pytest.raises(PermissionError):
        with data.gateway.transaction(data.sb,data.a):pass
    command('configure_account',account_id=data.a,archived=True)
    with data.gateway.transaction(data.sa,data.a):pass
    with pytest.raises(PermissionError):
        with data.gateway.transaction(data.sa,data.a,write=True):pass
    command('configure_user',user_id=member,enabled=False)
    with pytest.raises(PermissionError):data.gateway.overview(data.sb)


def test_broker_binding_audited_current_and_duplicate_rejected(data):
    def bind(account,number):return data.gateway.bind(data.sa,account,'securities',number,expected_revision=data.tx.snapshot(data.sa).revision)
    bind(data.a,'12345678')
    before=data.tx.snapshot(data.sa)
    with pytest.raises(ValueError):bind(data.b,'12345678')
    with pytest.raises(ValueError):data.gateway.bind(data.sa,data.b,'securities','87654321',expected_revision=0)
    assert data.tx.snapshot(data.sa)==before
    bind(data.b,'87654321')
    assert next(a for a in data.gateway.overview(data.sa) if a.id==data.a).suffixes==( ('securities','5678'), )
    service=DeploymentMonthlyInputs(data.gateway)
    with data.gateway.transaction(data.sa,data.a) as session:
        source=synthetic_sources('12345678')['thinkorswim_trade_history']
        service._validate_upload(session,'thinkorswim_trade_history',source)
        with pytest.raises(ValueError,match='different broker'):
            service._validate_upload(session,'thinkorswim_trade_history',synthetic_sources('87654321')['thinkorswim_trade_history'])


def test_all_selected_original_versions_checked_including_superseded(data):
    with data.gateway.transaction(data.sa,data.a) as session:
        identifier=uuid4();draft=SimpleNamespace(id=identifier,revision=2)
        session.drafts=SimpleNamespace(workspace=session._physical_scope,get=lambda _:draft)
        execute=session._connection.execute
        def rows(query,args=None):
            if 'SELECT d.sha256,d.content' in query:return Result(rows=[(sha256(b'original').hexdigest(),b'original'),('0'*64,b'corrupt older version')])
            return execute(query,args)
        session._connection.execute=rows
        with pytest.raises(ValueError,match='checksum'):DeploymentMonthlyInputs._lock(session,identifier)
        assert not any('FOR SHARE' in query for query,_ in session._connection.calls)


def test_published_month_save_rejected_before_upload(data):
    service=DeploymentMonthlyInputs(data.gateway);draft=SimpleNamespace(id=uuid4(),year=2025,month=1,revision=3)
    with patch.object(service,'_lock',return_value=draft),patch('campaigniq.account_reconciliation_preview.ReadOnlyArtifacts.list_keys',return_value=('2025-01-finalized.json',)):
        with pytest.raises(ValueError,match='Published month'):
            service.save(data.sa,data.a,draft.id,3,'thinkorswim_trade_history',synthetic_sources('12345678')['thinkorswim_trade_history'])
    assert not hasattr(service,'publish') and not hasattr(service,'published')


def test_schema_resolution_mismatch_never_adopts_other_tables(data):
    conn=data.base;original=conn.execute
    def wrong(query,args=None):
        if 'SELECT n.nspname,c.relname' in query:return Result(rows=[('public',n) for n in ('artifact_storage_dev','import_draft_documents','import_drafts')])
        return original(query,args)
    conn.execute=wrong
    with pytest.raises(ValueError,match='no fallback'):_open_session(conn,data.config,data.sa,data.a,write=True)


def test_caught_sql_failure_rejected_before_transaction_returns(data):
    with pytest.raises(AccountTransactionFailed):
        with data.gateway.transaction(data.sa,data.a) as session:session._connection.info.transaction_status=TransactionStatus.INERROR
    assert session._active is False


def test_legacy_reader_reuses_authorized_session_and_has_no_write_api(data):
    reader=DeploymentLegacyAccountAccess(data.gateway)
    assert [a.id for a in reader.choices(data.sa)]==[data.legacy]
    assert not hasattr(reader,'save') and not hasattr(reader,'publish')
    foreign=data.tx.snapshot(data.sa).accounts[1].id
    with pytest.raises(PermissionError):
        with reader._session(data.sa,foreign):pass


def test_runtime_data_role_demands_draft_only_writes_and_no_table_ownership():
    class Role:
        broad=False;owner=False
        def execute(self,query,args):
            if 'SELECT pg_has_role' in query:return Result(row=(self.owner,))
            table,privilege=args
            allowed={'SELECT','INSERT','UPDATE'} if table.startswith('campaigniq_accounts.import_draft') else {'SELECT'}
            return Result(row=(True if self.broad else privilege in allowed,))
    role=Role();validate_data_role(role)
    role.broad=True
    with pytest.raises(PermissionError,match='grants differ'):validate_data_role(role)
    role.broad=False;role.owner=True
    with pytest.raises(PermissionError,match='cannot own'):validate_data_role(role)


def test_constructors_and_modes_cannot_accept_local_or_forged_gateways(data):
    for value in (object(),None):
        with pytest.raises(ValueError):PostgresAccountDataGateway(value)
        with pytest.raises(ValueError):DeploymentMonthlyInputs(value)
        with pytest.raises(ValueError):DeploymentLegacyAccountAccess(value)
    with pytest.raises(ValueError):
        with data.gateway.transaction(data.sa,data.a,write=1):pass


def test_rehearsal_cleanup_refuses_different_checkpoint_and_never_cascades():
    from campaigniq import account_data_backend_rehearsal as rehearsal
    class Cleanup:
        def __init__(self):self.calls=[]
        def execute(self,query,args=None):
            self.calls.append(str(query));return Result(row=(1,))
    conn=Cleanup()
    with patch.object(rehearsal,'verify_schema'),patch.object(rehearsal,'schema_rows',return_value={'verified':'actual'}):
        with pytest.raises(ValueError,match='retained for recovery'):rehearsal._cleanup_rehearsal_schema(conn,{'verified':'different'})
        assert not any(q.startswith('DROP') for q in conn.calls)
        rehearsal._cleanup_rehearsal_schema(conn,{'verified':'actual'})
    drops=[q for q in conn.calls if q.startswith('DROP')]
    assert len(drops)==len(rehearsal.TABLES)+1 and all('CASCADE' not in q and 'public.' not in q for q in drops)


def test_rehearsal_cleanup_requires_verified_schema_and_preserves_missing_schema():
    from campaigniq import account_data_backend_rehearsal as rehearsal
    conn=SimpleNamespace(execute=lambda *args:Result(row=None))
    with patch.object(rehearsal,'verify_schema',side_effect=AssertionError('No adoption')):
        rehearsal._cleanup_rehearsal_schema(conn,None)


def test_original_row_verification_releases_observer_locks_before_cleanup():
    from contextlib import contextmanager
    from campaigniq import account_data_backend_rehearsal as rehearsal
    locks=set()
    observer=object()
    @contextmanager
    def connection(*,readonly,verify):
        assert readonly is True and verify is False
        try:yield observer
        finally:locks.clear()
    def read_inventory(conn):
        assert conn is observer
        locks.add('campaigniq_accounts.directories')
        return {'public.original':'unchanged','campaigniq_accounts.directories':'synthetic'}
    with patch.object(rehearsal,'inventory',side_effect=read_inventory):
        rehearsal._verify_original_rows(connection,{'public.original':'unchanged'})
        assert not locks
        with pytest.raises(ValueError,match='Original restore rows differ'):
            rehearsal._verify_original_rows(connection,{'public.original':'different'})
        assert not locks
