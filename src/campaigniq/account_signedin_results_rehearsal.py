"""LOCAL synthetic rehearsal of real-sign-in published results in a read-only snapshot."""
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
    issuer = 'https://signedin-results-rehearsal.invalid'
    admin, member = Identity(issuer,registry+'-a'), Identity(issuer,registry+'-b')
    scopes = ('workspace-results-'+registry+'-a','workspace-results-'+registry+'-b')
    initial = propose_legacy_directory(tuple(LegacyOwner(identity,scope,'Synthetic owner','Retained account','schwab')
        for identity,scope in zip((admin,member),scopes)),admin)
    gateway = LocalSignedInInputGateway(store,trusted_issuer=issuer,
        authorization={admin.subject:scopes[0],member.subject:scopes[1]})
    service = SignedInMonthlyInputs(gateway)
    sa,sb = RehearsalSession(admin),RehearsalSession(member)
    before = input_state(store,exclude_own=True)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    report = dict(format='campaigniq.signedin_results_rehearsal',version=1,registry_id=registry,
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
        from campaigniq import account_signedin_results as module
        service.publish(sa,a,da.id,revision,authorize_local_publication=True,forex_applicable=False,crypto_applicable=False)
        gateway.bind(sa,b,'securities','87654321',expected_revision=store._snapshot().revision)
        second_revision=0
        for role,upload in synthetic_sources('87654321',double=True).items():
            second_revision=service.save(sa,b,db.id,second_revision,role,upload)
        service.publish(sa,b,db.id,second_revision,authorize_local_publication=True,forex_applicable=False,crypto_applicable=False)
        def read(user=sa,target=a,identifier=da.id,rev=None):
            return service.results(user,target,identifier,revision if rev is None else rev)
        actual=module._results_session;observed=[];handles=[]
        def checked(*args,**kwargs):
            session=args[1];handles.append(session)
            observed.append((session._connection.execute('SHOW transaction_read_only').fetchone()[0],
                session._connection.execute('SHOW transaction_isolation').fetchone()[0],
                session._connection.execute('SELECT current_schema()').fetchone()[0]))
            return actual(*args,**kwargs)
        state=input_state(store,exclude_own=False)
        with patch.object(module,'_results_session',side_effect=checked):result=read()
        assert len(result.positions)==1 and Decimal(result.positions[0][1])==110
        assert result.original_documents==4 and Decimal(result.brokerage_realized_usd)==0
        assert observed==[('on','repeatable read',DATA_SCHEMA)]
        assert input_state(store,exclude_own=False)==state
        passed('committed_results_use_one_readonly_repeatable_snapshot_without_changing_any_row')
        independent=SignedInMonthlyInputs(LocalSignedInInputGateway(store,trusted_issuer=issuer,
            authorization={admin.subject:scopes[0],member.subject:scopes[1]}))
        assert independent.results(sa,a,da.id,revision)==result
        assert Decimal(read(target=b,identifier=db.id,rev=second_revision).positions[0][1])==220
        passed('independent_reader_keeps_two_accounts_and_their_published_results_separate')
        must_fail(ValueError,lambda:read(rev=0))
        must_fail(FileNotFoundError,lambda:read(target=b))
        must_fail(FileNotFoundError,lambda:read(identifier=db.id,rev=second_revision))
        must_fail(PermissionError,lambda:read(user=sb))
        must_fail(PermissionError,lambda:read(user=admin))
        must_fail(PermissionError,lambda:read(user=RehearsalSession(Identity('https://other.invalid',admin.subject))))
        assert input_state(store,exclude_own=False)==state
        passed('stale_foreign_and_unauthorized_requests_return_no_partial_results')

        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=Permission.VIEW)
        assert read(user=sb)==result
        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=None)
        must_fail(PermissionError,lambda:read(user=sb))
        edit('configure_account',account_id=a,archived=True)
        assert read()==result
        edit('configure_account',account_id=a,archived=False)
        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=Permission.VIEW)
        edit('configure_user',user_id=initial.users[1].id,enabled=False)
        must_fail(PermissionError,lambda:read(user=sb))
        passed('view_grants_revocation_archived_history_and_disabled_users_rechecked')

        state=input_state(store,exclude_own=False)
        def artifact_write(*args,**kwargs):args[1].artifacts.write_text('blocked.json','{}')
        def draft_write(*args,**kwargs):args[1].drafts.create_or_resume(2025,3)
        def sql_write(*args,**kwargs):
            args[1]._connection.execute('UPDATE campaigniq_signedin_accounts_dev.import_drafts SET revision=revision WHERE id=%s',(da.id,))
        for callback,kind in ((artifact_write,PermissionError),(draft_write,PermissionError),
                              (sql_write,psycopg.errors.ReadOnlySqlTransaction)):
            with patch.object(module,'_results_session',side_effect=callback):must_fail(kind,read)
            assert input_state(store,exclude_own=False)==state
        from campaigniq.persistence.account_data_session import ClosedAccountSession
        must_fail(ClosedAccountSession,lambda:handles[0].artifacts.read_text('2025-01-lot-book.json'))
        passed('artifact_draft_and_raw_sql_writes_blocked_and_read_handles_expire')

        from uuid import UUID
        namespace='signedin-'+UUID(registry).hex+'-'+UUID(a).hex
        def replace_artifact(key,text):
            with store._connection() as conn:
                conn.execute('UPDATE campaigniq_signedin_accounts_dev.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',
                    (text,namespace,key))
        with gateway.transaction(sa,a) as session:
            receipt=session.artifacts.read_text('2025-01-account-publication-receipt.json')
            realized=session.artifacts.read_text('2025-01-realized-attributions.json')
        changed=json.loads(receipt)
        changed['original_documents']['thinkorswim_trade_history']['sha256']='0'*64
        replace_artifact('2025-01-account-publication-receipt.json',json.dumps(changed))
        corrupted=input_state(store,exclude_own=False)
        must_fail(ValueError,read)
        assert input_state(store,exclude_own=False)==corrupted
        replace_artifact('2025-01-account-publication-receipt.json',receipt)
        passed('source_receipt_fingerprint_conflict_blocks_results_without_repair')
        changed=json.loads(realized);changed['period_end']='2025-02-28'
        replace_artifact('2025-01-realized-attributions.json',json.dumps(changed))
        corrupted=input_state(store,exclude_own=False)
        must_fail(ValueError,read)
        assert input_state(store,exclude_own=False)==corrupted
        replace_artifact('2025-01-realized-attributions.json',realized)
        assert read()==result
        passed('wrong_period_monetary_artifact_blocks_entire_report_without_partial_totals')
        assert input_state(store,exclude_own=True)==before
        passed('all_existing_financial_records_real_directory_and_prior_accounts_unchanged')
        report['local_rehearsal_passed']=True
    except Exception as error:
        report.update(local_rehearsal_passed=False,error_type=type(error).__name__)
        raise
    finally:
        with (output/'signedin-results-report.json').open('x') as f:
            json.dump(report,f,sort_keys=True,indent=2);f.write('\n')
    print('Report:',output/'signedin-results-report.json',flush=True)
    print('Only NEW synthetic local generations added for testing. Existing saved accounts and production unchanged.',flush=True)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--development-only',required=True,action='store_true')
    args=p.parse_args()
    dsn=os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL','dbname=campaigniq_dev')
    LocalPostgresAccountDirectory(dsn,str(uuid4()),development_only=args.development_only)
    output=Path.home()/'CampaignIQ-Records/Development-Rehearsals'/(
        'Signedin-results-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8])
    run_rehearsal(output,dsn)


if __name__=='__main__':main()
