"""Committed synthetic account storage on pinned local restore, then verified cleanup.

Only the newly created rehearsal schema is removed. All pre-existing tables stay
SHARE locked and untouched. No CASCADE, production target or real identity config.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor,TimeoutError
from contextlib import contextmanager
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import pwd
from threading import Event
from unittest.mock import patch
from uuid import uuid4
from campaigniq.account_administration import Identity,LegacyOwner,Permission
from campaigniq.account_backend_config import AccountDirectorySettings,AccountDatabaseTarget
from campaigniq.account_backend_rehearsal import ServerSession,rejected
from campaigniq.account_directory_bootstrap import review_bootstrap,bootstrap_directory
from campaigniq.account_schema_migrations import apply_migrations,verify_schema,catalog_digest,SCHEMA,TABLES,digest
from campaigniq.account_schema_rehearsal import restore_conninfo,inventory,DATABASE
from campaigniq.persistence.deployment_account_directory import PostgresAccountDirectory,DirectoryTransaction
from campaigniq.persistence.deployment_account_data import PostgresAccountDataGateway
from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
from campaigniq.account_deployment_legacy import DeploymentLegacyAccountAccess
from campaigniq.account_selection_rehearsal import synthetic_sources
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.import_drafts import DraftConflict
from campaigniq.persistence.account_data_session import AccountTransactionFailed,ClosedAccountSession
from campaigniq.account_legacy_results import _results


class MemoryStorage:
    def __init__(self,rows):self.rows=rows
    def exists(self,key):return key in self.rows
    def read_text(self,key):return self.rows[key]
    def list_keys(self,*,prefix='',suffix=''):
        return tuple(k for k in sorted(self.rows) if k.startswith(prefix) and k.endswith(suffix))


def save(path,value):
    with path.open('x') as handle:
        os.fchmod(handle.fileno(),0o600)
        json.dump(value,handle,sort_keys=True,indent=2);handle.write('\n');handle.flush();os.fsync(handle.fileno())
    descriptor=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def schema_rows(conn):
    rows={}
    for name in sorted(TABLES):
        values=conn.execute('SELECT md5(row_to_json(t)::text),count(*) FROM campaigniq_accounts.'+name+' t GROUP BY 1 ORDER BY 1').fetchall()
        rows[name]=digest(values)
    return dict(rows=rows,catalog_sha256=catalog_digest(conn))


def _cleanup_rehearsal_schema(conn,expected):
    """Caller is the pinned LOCAL runner and owns this cleanup transaction."""
    exists=conn.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(SCHEMA,)).fetchone()
    if not exists:return
    for name in sorted(TABLES):conn.execute('LOCK TABLE campaigniq_accounts.'+name+' IN ACCESS EXCLUSIVE MODE')
    verify_schema(conn)
    if expected is None or schema_rows(conn)!=expected:
        raise ValueError('New rehearsal schema differs from last verified checkpoint; retained for recovery')
    # RESTRICT is intentional. Any outside dependency stops cleanup atomically;
    # no unrelated schema or original table can be removed through CASCADE.
    order=('import_draft_documents','import_drafts','artifact_storage_dev','account_preferences','audit_events','directories','schema_migrations')
    if frozenset(order)!=TABLES:raise ValueError('Cleanup table inventory differs')
    for name in order:conn.execute('DROP TABLE campaigniq_accounts.'+name)
    conn.execute('DROP SCHEMA campaigniq_accounts')



def _verify_original_rows(local_connection,before):
    # Reading every table on the long-lived guard would retain ACCESS SHARE
    # locks on the new schema and block the separate cleanup transaction.
    # End this observer transaction before cleanup requests exclusive locks.
    with local_connection(readonly=True,verify=False) as observer:
        actual={k:v for k,v in inventory(observer).items() if not k.startswith(SCHEMA+'.')}
        if actual!=before:
            raise ValueError('Original restore rows differ; rehearsal verification failed')


def run_rehearsal(output,*,development_only):
    import psycopg
    conninfo=restore_conninfo(development_only=development_only)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    report=dict(format='campaigniq.account_data_backend_rehearsal',version=1,checks={},database=DATABASE,
                production_connection_made=False,account_mode_activated=False,publication_attempted=False)
    expected=None;created=False;checkpoint_number=0;before=None
    def passed(name):
        report['checks'][name]=True;print(name.replace('_',' ')+': PASS',flush=True)
    @contextmanager
    def local_connection(*,readonly,verify=True):
        with psycopg.connect(conninfo) as conn:
            if readonly:conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            conn.execute('SET LOCAL search_path=pg_catalog')
            conn.execute("SET LOCAL lock_timeout='5s'")
            conn.execute("SET LOCAL statement_timeout='30s'")
            actual=conn.execute('SELECT current_database(),current_user,inet_server_addr()').fetchone()
            if actual!=(DATABASE,pwd.getpwuid(os.getuid()).pw_name,None):raise ValueError('Unexpected local restore target')
            if verify:verify_schema(conn)
            yield conn
    def checkpoint():
        nonlocal expected,checkpoint_number
        with local_connection(readonly=True) as conn:snapshot=schema_rows(conn)
        checkpoint_number+=1
        save(output/f'checkpoint-{checkpoint_number:03d}.json',snapshot)
        expected=snapshot
    def mutate(call):
        result=call();checkpoint();return result
    def same():
        with local_connection(readonly=True) as conn:assert schema_rows(conn)==expected
    try:
        # Hold original table locks throughout independent committed operations.
        with local_connection(readonly=False,verify=False) as guard:
            if not guard.execute("SELECT pg_try_advisory_xact_lock(hashtextextended('campaigniq_accounts:restore-data-rehearsal',0))").fetchone()[0]:
                raise ValueError('Another local account storage rehearsal is active')
            if guard.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(SCHEMA,)).fetchone():
                raise ValueError('Restore already contains account schema; nothing is adopted or removed')
            before=inventory(guard,lock=True)
            scopes=tuple(row[0] for row in guard.execute('''SELECT namespace FROM public.artifact_storage_dev
                WHERE namespace LIKE 'workspace-%' UNION SELECT workspace FROM public.import_drafts
                WHERE workspace LIKE 'workspace-%' ORDER BY 1''').fetchall())
            if len(scopes)<2:raise ValueError('Two retained owner namespaces required')
            issuer='https://account-storage-rehearsal.invalid'
            identities=tuple(Identity(issuer,'synthetic-owner-'+str(i)) for i in range(len(scopes)))
            settings=AccountDirectorySettings(str(uuid4()),issuer,tuple(sorted((u.subject,w) for u,w in zip(identities,scopes))))
            owners=tuple(LegacyOwner(u,w,'Synthetic owner '+str(i),'Retained account','schwab') for i,(u,w) in enumerate(zip(identities,scopes)))
            plan=review_bootstrap(guard,settings,owners,identities[0])
            save(output/'prewrite-recovery-receipt.json',dict(database=DATABASE,registry_id=settings.registry_id,
                original_table_sha256=before,reviewed_bootstrap_sha256=plan.sha256,
                created_schema=SCHEMA,cleanup_policy='Only exact checkpoint-matching new schema, no CASCADE'))
            try:
                with local_connection(readonly=False,verify=False) as conn:
                    apply_migrations(conn);created=True
                checkpoint()
                with local_connection(readonly=False) as conn:
                    bootstrap_directory(conn,settings,plan,reviewed_sha256=plan.sha256)
                checkpoint()
                # The remote wrapper is constructed offline. Scoped test injection
                # routes every service connection to the same pinned LOCAL factory;
                # no deployment endpoint or relaxed production guard is accepted.
                target=AccountDatabaseTarget('never-connected.invalid','not_connected','not_connected')
                store=PostgresAccountDirectory('host=never-connected.invalid port=5432 dbname=not_connected user=not_connected sslmode=verify-full',settings,target)
                gateway=PostgresAccountDataGateway(store)
                sa,sb=map(ServerSession,identities[:2])
                with patch.object(store,'_connection',new=local_connection),patch.object(gateway,'_connection',new=local_connection):
                    service=DeploymentMonthlyInputs(gateway);legacy=DeploymentLegacyAccountAccess(gateway)
                    def command(action,**values):
                        return mutate(lambda:store.execute(sa,store.snapshot(sa).revision,action,**values))
                    owner=store.snapshot(sa).user(identities[0]).id
                    for name in ('Synthetic A','Synthetic B'):command('add_account',owner_id=owner,name=name,broker='schwab')
                    a,b=(x.id for x in store.snapshot(sa).accounts if not x.legacy)
                    la=next(x.id for x in plan.directory.accounts if x.owner_id==plan.directory.user(identities[0]).id)
                    rejected(ValueError,lambda:service.create(sa,la,2025,1))
                    rejected(PermissionError,lambda:service.create(sb,a,2025,1))
                    da=mutate(lambda:service.create(sa,a,2025,1));db=mutate(lambda:service.create(sa,b,2025,1))
                    assert da.workspace!=db.workspace and da.id!=db.id
                    assert da.workspace==store.authorize(sa,a).storage_scope
                    passed('same_month_new_account_drafts_are_isolated_and_legacy_uploads_blocked')
                    def binding(account,number,**kwargs):
                        return mutate(lambda:gateway.bind(sa,account,'securities',number,expected_revision=store.snapshot(sa).revision,**kwargs))
                    binding(a,'12345678')
                    rejected(ValueError,lambda:binding(b,'12345678'))
                    binding(b,'87654321')
                    sources=synthetic_sources('12345678');role='thinkorswim_trade_history'
                    rejected(ValueError,lambda:service.save(sa,a,da.id,0,role,synthetic_sources('87654321')[role]))
                    same()
                    passed('audited_binding_and_wrong_account_document_checks_reuse_current_identity_rules')
                    revision=mutate(lambda:service.save(sa,a,da.id,0,role,sources[role]))
                    replacement=RestoredUpload('replacement.csv','text/csv',sources[role].content+b'\n')
                    rejected(DraftConflict,lambda:service.save(sa,a,da.id,revision,role,replacement))
                    rejected(ValueError,lambda:service.save(sa,a,da.id,0,role,replacement,replace=True))
                    rejected(FileNotFoundError,lambda:service.save(sa,b,da.id,revision,role,replacement))
                    revision=mutate(lambda:service.save(sa,a,da.id,revision,role,replacement,replace=True))
                    with gateway.transaction(sa,a) as session:
                        assert session.drafts.document(da.id,role)[2]==replacement.content
                        rows=session._connection.execute('SELECT content FROM import_draft_documents WHERE draft_id=%s AND superseded=true',(da.id,)).fetchall()
                        assert [bytes(row[0]) for row in rows]==[sources[role].content]
                    passed('foreign_and_stale_drafts_rejected_replacement_preserves_exact_original_bytes')
                    for other,upload in sources.items():
                        if other!=role:revision=mutate(lambda:service.save(sa,a,da.id,revision,other,upload))
                    preview=service.preview(sa,a,da.id,revision,forex_applicable=False,crypto_applicable=False)
                    result=service.reconcile(sa,a,da.id,revision,forex_applicable=False,crypto_applicable=False)
                    assert preview.inputs_ready and result.inventory_reconciled
                    assert result.closing_quantities[0][1]=='110.0000' or float(result.closing_quantities[0][1])==110
                    assert not result.publication_authorized and not preview.publication_authorized
                    same()
                    passed('bound_saved_month_previews_and_reconciles_to_110_shares_without_writes')
                    def failed_save(caught=False):
                        with gateway.transaction(sa,a,write=True) as session:
                            session.drafts.upload(da.id,'rollback-test','test.csv','text/csv',b'synthetic rollback')
                            if caught:rejected(psycopg.errors.DivisionByZero,lambda:session._connection.execute('SELECT 1/0'))
                            else:raise RuntimeError('injected draft failure')
                    rejected(RuntimeError,lambda:failed_save())
                    rejected(AccountTransactionFailed,lambda:failed_save(True))
                    same()
                    passed('draft_document_revision_and_caught_sql_failures_roll_back_without_partial_commit')
                    def readonly_write():
                        with gateway.transaction(sa,a) as session:
                            rejected(PermissionError,lambda:session.artifacts.write_text('blocked.json','{}'))
                            rejected(psycopg.errors.ReadOnlySqlTransaction,lambda:session._connection.execute("UPDATE artifact_storage_dev SET content='blocked'"))
                    rejected(AccountTransactionFailed,readonly_write)
                    with gateway.transaction(sa,a,write=True) as session:
                        rejected(PermissionError,lambda:session.artifacts.write_text('blocked.json','{}'))
                    with gateway.transaction(sa,a) as expired:pass
                    rejected(ClosedAccountSession,lambda:expired.drafts.list_drafts())
                    same()
                    passed('readonly_sql_and_artifact_write_guards_hold_and_account_handles_expire')
                    with gateway.transaction(sa,a) as session:
                        old_id,old_bytes=session._connection.execute('SELECT id,content FROM import_draft_documents WHERE draft_id=%s AND superseded=true',(da.id,)).fetchone()
                        old_bytes=bytes(old_bytes)
                    def content(value):
                        with local_connection(readonly=False) as conn:
                            conn.execute('UPDATE campaigniq_accounts.import_draft_documents SET content=%s WHERE id=%s',(value,old_id))
                    mutate(lambda:content(b'corrupt synthetic original'))
                    rejected(ValueError,lambda:service.preview(sa,a,da.id,revision,forex_applicable=False,crypto_applicable=False))
                    rejected(ValueError,lambda:service.save(sa,a,da.id,revision,role,replacement,replace=True))
                    same();mutate(lambda:content(old_bytes))
                    binding(a,'11223344',confirm_replace=True)
                    assert not service.preview(sa,a,da.id,revision,forex_applicable=False,crypto_applicable=False).inputs_ready
                    same();binding(a,'12345678',confirm_replace=True)
                    passed('superseded_original_corruption_and_changed_broker_identity_block_use_without_repair')
                    # Independent connections must not lose a competing upload.
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        futures=[pool.submit(service.create,sa,a,2025,2) for _ in range(2)]
                        drafts=[future.result(timeout=15) for future in futures]
                    assert drafts[0].id==drafts[1].id
                    checkpoint()
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        alternatives=[RestoredUpload('concurrent.csv','text/csv',replacement.content+b'\n'*n) for n in (1,2)]
                        futures=[pool.submit(service.save,sa,a,da.id,revision,role,item,replace=True) for item in alternatives]
                        outcomes=[]
                        for future in futures:
                            try:outcomes.append(future.result(timeout=15))
                            except ValueError as error:
                                if 'changed' not in str(error):raise
                                outcomes.append('stale')
                    assert outcomes.count('stale')==1 and sum(type(item) is int for item in outcomes)==1
                    revision=next(item for item in outcomes if type(item) is int)
                    checkpoint()
                    passed('independent_concurrent_create_is_idempotent_and_stale_upload_cannot_overwrite_winner')
                    member=store.snapshot(sa).user(identities[1]).id
                    command('set_grant',user_id=member,account_id=a,permission=Permission.VIEW)
                    rejected(PermissionError,lambda:service.create(sb,a,2025,3))
                    started=Event()
                    def revoke():
                        started.set()
                        return store.execute(sa,store.snapshot(sa).revision,'set_grant',user_id=member,account_id=a,permission=None)
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        with gateway.transaction(sb,a) as session:
                            assert session.drafts.get(da.id).revision==revision
                            future=pool.submit(revoke)
                            assert started.wait(5)
                            try:future.result(timeout=0.15)
                            except TimeoutError:pass
                            else:raise AssertionError('Revocation committed before active account read ended')
                        future.result(timeout=15)
                    checkpoint()
                    rejected(PermissionError,lambda:service.drafts(sb,a))
                    command('configure_account',account_id=a,archived=True)
                    assert service.drafts(sa,a)
                    rejected(PermissionError,lambda:service.create(sa,a,2025,3))
                    command('configure_account',account_id=a,archived=False)
                    command('set_grant',user_id=member,account_id=a,permission=Permission.VIEW)
                    command('configure_user',user_id=member,enabled=False)
                    rejected(PermissionError,lambda:service.drafts(sb,a))
                    command('configure_user',user_id=member,enabled=True)
                    passed('view_revocation_waits_for_active_snapshot_archive_and_disabled_access_rechecked')
                    # Compare each retained account and every visible published
                    # period with the canonical reader fed the original bytes.
                    periods=0;originals=0
                    for identity in identities:
                        user=ServerSession(identity)
                        account=next(x.id for x in plan.directory.accounts if x.owner_id==plan.directory.user(identity).id)
                        info=legacy.inventory(user,account);assert info.adapter_parity_verified
                        originals+=info.document_versions
                        for month_text in legacy.published_months(user,account):
                            year,month=map(int,month_text.split('-'))
                            actual=legacy.published_results(user,account,year,month)
                            with gateway.legacy_transaction(user,account) as (conn,session,_):
                                direct=conn.execute('SELECT artifact_key,content FROM public.artifact_storage_dev WHERE namespace=%s ORDER BY artifact_key',(session.account.storage_scope,)).fetchall()
                                storage=MemoryStorage(dict(direct));prefix=month_text+'-'
                                expected_result=_results(storage,account,year,month,[(k,v) for k,v in direct if k.startswith(prefix)])
                            assert actual==expected_result
                            periods+=1
                    rejected(PermissionError,lambda:legacy.inventory(sa,next(x.id for x in plan.directory.accounts if x.owner_id==plan.directory.user(identities[1]).id)))
                    assert periods>0
                    report.update(retained_published_periods_verified=periods,retained_original_versions_verified=originals)
                    same()
                    passed('all_retained_account_originals_and_published_period_results_match_canonical_sources')
                    with local_connection(readonly=True) as conn:
                        assert conn.execute('SELECT count(*) FROM campaigniq_accounts.artifact_storage_dev').fetchone()[0]==0
                        assert conn.execute('SELECT count(*) FROM campaigniq_accounts.account_preferences').fetchone()[0]==0
                    _verify_original_rows(local_connection,before)
                    passed('all_existing_financial_tables_originals_preferences_and_prior_registries_unchanged')
            finally:
                if created:
                    with local_connection(readonly=False,verify=False) as conn:
                        _cleanup_rehearsal_schema(conn,expected)
                    report['new_schema_cleanup_verified']=True
            with local_connection(readonly=True,verify=False) as observer:
                assert not observer.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(SCHEMA,)).fetchone()
                assert inventory(observer)==before
            passed('independent_reader_confirms_all_original_rows_unchanged_and_only_new_schema_removed')
        report['passed']=True
    except Exception as error:
        report.update(passed=False,error_type=type(error).__name__);raise
    finally:
        save(output/'account-data-backend-report.json',report)
        print('Report:',output/'account-data-backend-report.json')
    print('Local full restore unchanged. Only the rehearsal schema was removed. Production unchanged.')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only',action='store_true',required=True)
    args=parser.parse_args()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    run_rehearsal(Path.home()/'CampaignIQ-Records/Development-Rehearsals'/('Account-data-backend-'+stamp),development_only=args.development_only)


if __name__=='__main__':main()
