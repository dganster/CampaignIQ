"""Seed NEW local legacy test workspaces and verify read-only saved results."""
import argparse
from contextlib import contextmanager
from datetime import datetime,timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from campaigniq.account_administration import Identity,LegacyOwner,Permission,propose_legacy_directory
from campaigniq.account_directory_rehearsal import must_fail
from campaigniq.account_legacy_access import LocalLegacyAccountAccess
from campaigniq.account_legacy_access_rehearsal import RehearsalSession,protected_rows,seed_sources
from campaigniq.account_selection_rehearsal import synthetic_sources
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory


class _MemoryArtifacts:
    def __init__(self):self.rows={}
    def exists(self,key):return key in self.rows
    def read_text(self,key):return self.rows[key]
    def write_text(self,key,text):self.rows[key]=text
    def delete(self,key):self.rows.pop(key,None)
    def list_keys(self,*,prefix='',suffix=''):
        return tuple(sorted(k for k in self.rows if k.startswith(prefix) and k.endswith(suffix)))


def _candidate(number,*,double=False):
    from campaigniq.monthly_upload_staging import stage_monthly_uploads
    from campaigniq.import_preflight import prepare_monthly_import
    from campaigniq.monthly_import_execution import execute_monthly_import
    storage=_MemoryArtifacts()
    with TemporaryDirectory(prefix='campaigniq-retained-results-fixture-') as folder:
        root=Path(folder)
        supplied=stage_monthly_uploads(upload_dir=root,uploads=synthetic_sources(number,double=double))
        preflight=prepare_monthly_import(2025,1,authoritative_state_root=root/'state',supplied_inputs=supplied,
            artifact_storage=storage,forex_applicable=False,crypto_applicable=False)
        assert preflight.ready
        outcome=execute_monthly_import(preflight,authoritative_state_root=root/'state',supplied_inputs=supplied,artifact_storage=storage)
        assert outcome.finalized and outcome.closing_reconciliation.reconciled
    return storage.rows


def run_rehearsal(output,dsn='dbname=campaigniq_dev'):
    import psycopg
    registry=str(uuid4());store=LocalPostgresAccountDirectory(dsn,registry,development_only=True)
    issuer='https://legacy-results-rehearsal.invalid'
    admin,member=Identity(issuer,registry+'-a'),Identity(issuer,registry+'-b')
    scopes=('workspace-legacy-results-'+registry+'-a','workspace-legacy-results-'+registry+'-b')
    initial=propose_legacy_directory(tuple(LegacyOwner(identity,scope,'Synthetic owner','Synthetic retained account','schwab')
        for identity,scope in zip((admin,member),scopes)),admin)
    reader=LocalLegacyAccountAccess(store,trusted_issuer=issuer,authorization={admin.subject:scopes[0],member.subject:scopes[1]})
    before=protected_rows(store,scopes)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    report=dict(format='campaigniq.legacy_results_rehearsal',version=1,registry_id=registry,
        production_connection_made=False,checks={})
    def passed(name):
        report['checks'][name]=True;print(name.replace('_',' ')+': PASS',flush=True)
    def edit(action,/,**values):store.execute(admin,store._snapshot().revision,action,**values)
    try:
        store.initialize();store.bootstrap(initial);seed_sources(store,scopes)
        for scope,number,double in ((scopes[0],'12345678',False),(scopes[1],'87654321',True)):
            with store._connection() as conn:
                for key,text in _candidate(number,double=double).items():
                    conn.execute('INSERT INTO public.artifact_storage_dev(namespace,artifact_key,content) VALUES(%s,%s,%s)',(scope,key,text))
        a,b=(account.id for account in initial.accounts)
        sa,sb=RehearsalSession(admin),RehearsalSession(member)
        result=reader.published_results(sa,a,2025,1)
        assert Decimal(result.positions[0][1])==110 and Decimal(reader.published_results(sb,b,2025,1).positions[0][1])==220
        assert Decimal(result.brokerage_realized_usd)==0 and result.source_artifacts>0
        assert reader.published_months(sa,a)==('2025-01',)
        passed('canonical_saved_results_preserve_exact_legacy_workspace_isolation')
        actual=reader._session;observed=[];handles=[]
        @contextmanager
        def observe(*args,**kwargs):
            with actual(*args,**kwargs) as values:
                conn,session,_=values;handles.append(session)
                observed.append((conn.execute('SHOW transaction_read_only').fetchone()[0],
                    conn.execute('SHOW transaction_isolation').fetchone()[0],session.account.storage_scope))
                yield values
        state=protected_rows(store,(),exclude_registry=False)
        with patch.object(reader,'_session',observe):assert reader.published_results(sa,a,2025,1)==result
        assert observed==[('on','repeatable read',scopes[0])]
        assert protected_rows(store,(),exclude_registry=False)==state
        passed('source_adapter_parity_uses_one_readonly_repeatable_snapshot_without_row_changes')
        must_fail(PermissionError,lambda:reader.published_results(sa,b,2025,1))
        must_fail(PermissionError,lambda:reader.published_results(sb,a,2025,1))
        must_fail(PermissionError,lambda:reader.published_results(RehearsalSession(Identity('https://other.invalid',admin.subject)),a,2025,1))
        must_fail(PermissionError,lambda:reader.published_results(RehearsalSession(admin,logged_in=False),a,2025,1))
        assert protected_rows(store,(),exclude_registry=False)==state
        passed('admin_has_no_implicit_cross_owner_access_and_current_signin_is_required')
        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=Permission.VIEW)
        assert reader.published_results(sb,a,2025,1)==result
        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=None)
        must_fail(PermissionError,lambda:reader.published_results(sb,a,2025,1))
        edit('configure_account',account_id=a,archived=True)
        assert reader.published_results(sa,a,2025,1)==result
        edit('configure_user',user_id=initial.users[1].id,enabled=False)
        must_fail(PermissionError,lambda:reader.published_months(sb,b))
        passed('view_revocation_archiving_and_disabled_owner_permissions_rechecked')
        state=protected_rows(store,(),exclude_registry=False)
        from campaigniq.persistence.account_data_session import AccountTransactionFailed,ClosedAccountSession
        with reader._session(sa,a) as (_,session,_):
            must_fail(PermissionError,lambda:session.artifacts.write_text('blocked.json','{}'))
            must_fail(PermissionError,lambda:session.drafts.create_or_resume(2025,3))
        def inject_sql():
            with reader._session(sa,a) as (conn,_,_):
                must_fail(psycopg.errors.ReadOnlySqlTransaction,lambda:conn.execute(
                    'UPDATE public.artifact_storage_dev SET content=content WHERE namespace=%s',(scopes[0],)))
        must_fail(AccountTransactionFailed,inject_sql)
        must_fail(ClosedAccountSession,lambda:handles[0].artifacts.list_keys())
        assert protected_rows(store,(),exclude_registry=False)==state
        passed('python_and_raw_sql_writes_blocked_and_handles_expire')
        with store._connection() as conn:
            removed=conn.execute('DELETE FROM public.artifact_storage_dev WHERE namespace=%s AND artifact_key=%s RETURNING artifact_key,content,updated_at',
                (scopes[0],'2025-01-forex-settlement-attributions.json')).fetchone()
        try:
            unknown=reader.published_results(sa,a,2025,1)
            assert unknown.forex_settled_usd is None and unknown.forex_records is None
            assert any('coverage is unknown' in warning for warning in unknown.warnings)
        finally:
            with store._connection() as conn:
                conn.execute('INSERT INTO public.artifact_storage_dev(namespace,artifact_key,content,updated_at) VALUES(%s,%s,%s,%s)',(scopes[0],*removed))
        assert protected_rows(store,(),exclude_registry=False)==state
        passed('missing_forex_evidence_is_unknown_coverage_not_a_zero_subtotal')
        key='2025-01-realized-attributions.json'
        with store._connection() as conn:
            text=conn.execute('SELECT content FROM public.artifact_storage_dev WHERE namespace=%s AND artifact_key=%s',(scopes[0],key)).fetchone()[0]
            wrong=json.loads(text);wrong['period_end']='2025-02-28'
            conn.execute('UPDATE public.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',(json.dumps(wrong),scopes[0],key))
        try:must_fail(ValueError,lambda:reader.published_results(sa,a,2025,1))
        finally:
            with store._connection() as conn:
                conn.execute('UPDATE public.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',(text,scopes[0],key))
        assert reader.published_results(sa,a,2025,1)==result
        assert protected_rows(store,(),exclude_registry=False)==state
        passed('wrong_period_financial_record_blocks_entire_report_without_repair')
        assert protected_rows(store,scopes)==before
        passed('all_existing_financial_records_real_directory_and_prior_accounts_unchanged')
        report['local_rehearsal_passed']=True
    except Exception as error:
        report.update(local_rehearsal_passed=False,error_type=type(error).__name__);raise
    finally:
        with (output/'legacy-results-report.json').open('x') as f:json.dump(report,f,sort_keys=True,indent=2);f.write('\n')
    print('Report:',output/'legacy-results-report.json',flush=True)
    print('Only NEW synthetic local legacy workspaces added. Existing saved accounts and production unchanged.',flush=True)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--development-only',required=True,action='store_true');args=p.parse_args()
    dsn=os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL','dbname=campaigniq_dev')
    LocalPostgresAccountDirectory(dsn,str(uuid4()),development_only=args.development_only)
    output=Path.home()/'CampaignIQ-Records/Development-Rehearsals'/(
        'Legacy-results-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8])
    run_rehearsal(output,dsn)


if __name__=='__main__':main()
