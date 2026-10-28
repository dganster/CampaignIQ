"""LOCAL synthetic rehearsal of real-sign-in published account comparison in one read-only snapshot."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from campaigniq.account_administration import Identity, LegacyOwner, Permission, propose_legacy_directory
from campaigniq.account_directory_rehearsal import must_fail
from campaigniq.account_legacy_access_rehearsal import RehearsalSession
from campaigniq.account_selection_rehearsal import synthetic_sources
from campaigniq.account_signedin_inputs import SignedInMonthlyInputs
from campaigniq.account_signedin_inputs_rehearsal import input_state
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
from campaigniq.persistence.signedin_account_data import DATA_SCHEMA, LocalSignedInInputGateway


def run_rehearsal(output, dsn='dbname=campaigniq_dev'):
    import psycopg
    registry = str(uuid4())
    store = LocalPostgresAccountDirectory(dsn, registry, development_only=True)
    issuer = 'https://signedin-comparison-rehearsal.invalid'
    admin, member = Identity(issuer,registry+'-a'), Identity(issuer,registry+'-b')
    scopes = ('workspace-compare-'+registry+'-a','workspace-compare-'+registry+'-b')
    initial = propose_legacy_directory(tuple(LegacyOwner(identity,scope,'Synthetic owner','Retained account','schwab')
        for identity,scope in zip((admin,member),scopes)),admin)
    gateway = LocalSignedInInputGateway(store,trusted_issuer=issuer,
        authorization={admin.subject:scopes[0],member.subject:scopes[1]})
    service = SignedInMonthlyInputs(gateway)
    sa,sb = RehearsalSession(admin),RehearsalSession(member)
    before = input_state(store,exclude_own=True)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    report = dict(format='campaigniq.signedin_comparison_rehearsal',version=1,registry_id=registry,
        checks={},production_connection_made=False,production_publication_authorized=False)
    def passed(name):
        report['checks'][name]=True
        print(name.replace('_',' ')+': PASS',flush=True)
    def edit(action,/,**values):
        return store.execute(admin,store._snapshot().revision,action,**values)
    try:
        store.initialize();store.bootstrap(initial)
        for name in ('Synthetic A','Synthetic B'):
            edit('add_account',owner_id=initial.users[0].id,name=name,broker='schwab')
        a,b = (account.id for account in store._snapshot().accounts if not account.legacy)
        gateway.initialize(sa)
        gateway.bind(sa,a,'securities','12345678',expected_revision=store._snapshot().revision)
        da,db = service.create(sa,a,2025,1),service.create(sa,b,2025,1)
        sources = synthetic_sources('12345678')
        revision=0
        for role,upload in sources.items():revision=service.save(sa,a,da.id,revision,role,upload)
        from campaigniq import account_signedin_comparison as module
        service.publish(sa,a,da.id,revision,authorize_local_publication=True,forex_applicable=False,crypto_applicable=False)
        gateway.bind(sa,b,'securities','87654321',expected_revision=store._snapshot().revision)
        second_revision=0
        for role,upload in synthetic_sources('87654321',double=True).items():
            second_revision=service.save(sa,b,db.id,second_revision,role,upload)
        service.publish(sa,b,db.id,second_revision,authorize_local_publication=True,forex_applicable=False,crypto_applicable=False)
        from campaigniq.account_signedin_comparison import PublishedSelection
        selections=(PublishedSelection(a,da.id,revision),PublishedSelection(b,db.id,second_revision))
        def compare(user=sa,items=selections,totals=False):
            return service.compare(user,2025,1,items,include_positions=totals)
        actual=module._results_session;observed=[];handles=[]
        def checked(*args,**kwargs):
            session=args[1];handles.append(session)
            observed.append((session._connection,session._connection.execute('SHOW transaction_read_only').fetchone()[0],
                session._connection.execute('SHOW transaction_isolation').fetchone()[0],
                session._connection.execute('SELECT current_schema()').fetchone()[0]))
            return actual(*args,**kwargs)
        state=input_state(store,exclude_own=False)
        with patch.object(module,'_results_session',side_effect=checked):result=compare()
        assert [Decimal(row.positions[0][1]) for row in result.accounts]==[110,220]
        assert len(observed)==2 and observed[0][0] is observed[1][0]
        assert all(row[1:]==('on','repeatable read',DATA_SCHEMA) for row in observed)
        assert input_state(store,exclude_own=False)==state
        passed('entire_comparison_reads_one_readonly_repeatable_snapshot_without_row_changes')
        assert not result.includes_combined_positions and result.combined_positions==()
        total=compare(totals=True)
        assert Decimal(total.combined_positions[0][1])==330
        independent=SignedInMonthlyInputs(LocalSignedInInputGateway(store,trusted_issuer=issuer,
            authorization={admin.subject:scopes[0],member.subject:scopes[1]}))
        assert independent.compare(sa,2025,1,selections)==result
        passed('account_results_stay_separate_and_combined_330_quantity_requires_opt_in')

        from dataclasses import replace
        must_fail(ValueError,lambda:compare(items=(selections[0],)*2))
        must_fail(ValueError,lambda:compare(items=(selections[0],replace(selections[1],revision=0))))
        must_fail(FileNotFoundError,lambda:compare(items=(selections[0],replace(selections[1],draft_id=da.id))))
        must_fail(PermissionError,lambda:compare(user=admin))
        assert input_state(store,exclude_own=False)==state
        passed('duplicate_stale_foreign_and_forged_identity_requests_return_no_report')
        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=Permission.VIEW)
        must_fail(PermissionError,lambda:compare(user=sb))
        edit('set_grant',user_id=initial.users[1].id,account_id=b,permission=Permission.VIEW)
        assert compare(user=sb)==result
        edit('configure_account',account_id=b,archived=True)
        assert compare()==result
        edit('set_grant',user_id=initial.users[1].id,account_id=b,permission=None)
        must_fail(PermissionError,lambda:compare(user=sb))
        edit('configure_user',user_id=initial.users[1].id,enabled=False)
        must_fail(PermissionError,lambda:compare(user=sb))
        passed('whole_selection_view_access_revocation_disabled_users_and_archived_history_rechecked')

        state=input_state(store,exclude_own=False)
        def artifact_write(*args,**kwargs):args[1].artifacts.write_text('blocked.json','{}')
        def draft_write(*args,**kwargs):args[1].drafts.create_or_resume(2025,3)
        def sql_write(*args,**kwargs):
            args[1]._connection.execute('UPDATE campaigniq_signedin_accounts_dev.import_drafts SET revision=revision WHERE id=%s',(da.id,))
        for callback,kind in ((artifact_write,PermissionError),(draft_write,PermissionError),
                              (sql_write,psycopg.errors.ReadOnlySqlTransaction)):
            with patch.object(module,'_results_session',side_effect=callback):must_fail(kind,compare)
            assert input_state(store,exclude_own=False)==state
        from campaigniq.persistence.account_data_session import ClosedAccountSession
        must_fail(ClosedAccountSession,lambda:handles[1].artifacts.read_text('2025-01-lot-book.json'))
        passed('python_and_raw_sql_writes_blocked_and_all_read_handles_expire')

        from uuid import UUID
        namespace='signedin-'+UUID(registry).hex+'-'+UUID(b).hex
        def replace_artifact(key,text):
            with store._connection() as conn:
                conn.execute('UPDATE campaigniq_signedin_accounts_dev.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',
                    (text,namespace,key))
        with gateway.transaction(sa,b) as session:
            lot_text=session.artifacts.read_text('2025-01-lot-book.json')
            receipt=session.artifacts.read_text('2025-01-account-publication-receipt.json')
        changed=json.loads(lot_text)
        for row in changed['lots']:row['quantity']='999'
        injected=[]
        def concurrent_change(*args,**kwargs):
            value=actual(*args,**kwargs)
            if not injected:
                replace_artifact('2025-01-lot-book.json',json.dumps(changed))
                injected.append(True)
            return value
        try:
            with patch.object(module,'_results_session',side_effect=concurrent_change):snapshot=compare()
            assert injected and snapshot==result
        finally:
            replace_artifact('2025-01-lot-book.json',lot_text)
        assert input_state(store,exclude_own=False)==state
        passed('concurrent_artifact_commit_cannot_mix_generations_in_the_returned_snapshot')
        changed=json.loads(receipt)
        changed['original_documents']['thinkorswim_trade_history']['sha256']='0'*64
        replace_artifact('2025-01-account-publication-receipt.json',json.dumps(changed))
        corrupted=input_state(store,exclude_own=False)
        try:
            must_fail(ValueError,lambda:compare(totals=True))
            assert input_state(store,exclude_own=False)==corrupted
        finally:
            replace_artifact('2025-01-account-publication-receipt.json',receipt)
        assert compare()==result
        passed('one_conflicting_source_receipt_withholds_every_result_and_combined_total')
        assert input_state(store,exclude_own=True)==before
        passed('all_existing_financial_records_real_directory_and_prior_accounts_unchanged')
        report['local_rehearsal_passed']=True
    except Exception as error:
        report.update(local_rehearsal_passed=False,error_type=type(error).__name__)
        raise
    finally:
        with (output/'signedin-comparison-report.json').open('x') as f:
            json.dump(report,f,sort_keys=True,indent=2);f.write('\n')
    print('Report:',output/'signedin-comparison-report.json',flush=True)
    print('Only NEW synthetic local generations added for testing. Existing saved accounts and production unchanged.',flush=True)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--development-only',required=True,action='store_true')
    args=p.parse_args()
    dsn=os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL','dbname=campaigniq_dev')
    LocalPostgresAccountDirectory(dsn,str(uuid4()),development_only=args.development_only)
    output=Path.home()/'CampaignIQ-Records/Development-Rehearsals'/(
        'Signedin-comparison-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8])
    run_rehearsal(output,dsn)


if __name__=='__main__':main()
