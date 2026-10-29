"""LOCAL account-aware dashboard snapshot rehearsal using new synthetic sources only."""
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
    issuer = 'https://dashboard-rehearsal.invalid'
    admin, member = Identity(issuer,registry+'-a'), Identity(issuer,registry+'-b')
    scopes = ('workspace-dashboard-'+registry+'-a','workspace-dashboard-'+registry+'-b')
    initial = propose_legacy_directory(tuple(LegacyOwner(identity,scope,'Synthetic owner','Retained account','schwab')
        for identity,scope in zip((admin,member),scopes)),admin)
    gateway = LocalSignedInInputGateway(store,trusted_issuer=issuer,
        authorization={admin.subject:scopes[0],member.subject:scopes[1]})
    service = SignedInMonthlyInputs(gateway)
    sa,sb = RehearsalSession(admin),RehearsalSession(member)
    before = input_state(store,exclude_own=True)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    report = dict(format='campaigniq.account_dashboard_rehearsal',version=1,registry_id=registry,
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
        from campaigniq.account_dashboard import LocalAccountDashboard
        from campaigniq import account_dashboard as dashboard_module
        from campaigniq.persistence.lot_book_store import load_lot_book_from_storage
        dashboard=LocalAccountDashboard(store,trusted_issuer=issuer,
            authorization={admin.subject:scopes[0],member.subject:scopes[1]})
        def read(user=sa,target=a):return dashboard.snapshot(user,target)
        observed=[];handles=[];actual=dashboard_module._results_session
        def checked(*args,**kwargs):
            session=args[1];handles.append(session)
            observed.append((session._connection.execute('SHOW transaction_read_only').fetchone()[0],
                session._connection.execute('SHOW transaction_isolation').fetchone()[0],
                session._connection.execute('SELECT current_schema()').fetchone()[0]))
            return actual(*args,**kwargs)
        state=input_state(store,exclude_own=False)
        with patch.object(dashboard_module,'_results_session',side_effect=checked):snapshot=read()
        assert observed==[('on','repeatable read',DATA_SCHEMA)]
        assert input_state(store,exclude_own=False)==state
        assert all(not s._active for s in handles)
        passed('dashboard_sources_read_one_readonly_repeatable_snapshot_and_expire_handles')
        def quantity(snapshot):
            lots=load_lot_book_from_storage(snapshot,'2025-01-lot-book.json').lot_book
            return sum(lot.quantity for values in lots._lots.values() for lot in values)
        assert quantity(snapshot)==110 and quantity(read(target=b))==220
        assert snapshot.namespace!=read(target=b).namespace
        assert len(dashboard.choices(sa).accounts)==3  # own retained + two new
        assert len(dashboard.choices(sb).accounts)==1  # own retained only
        must_fail(PermissionError,lambda:read(user=sb))
        passed('account_choices_and_published_110_and_220_share_reports_remain_isolated')
        for action in (lambda:snapshot.write_text('blocked','x'),lambda:snapshot.delete('x'),snapshot.initialize):
            must_fail(PermissionError,action)
        def artifact_write(*args,**kwargs):args[1].artifacts.write_text('blocked.json','{}')
        def draft_write(*args,**kwargs):args[1].drafts.create_or_resume(2025,3)
        def sql_write(*args,**kwargs):
            args[1]._connection.execute('UPDATE campaigniq_signedin_accounts_dev.import_drafts SET revision=revision WHERE id=%s',(da.id,))
        for callback,kind in ((artifact_write,PermissionError),(draft_write,PermissionError),
                              (sql_write,psycopg.errors.ReadOnlySqlTransaction)):
            with patch.object(dashboard_module,'_results_session',side_effect=callback):must_fail(kind,read)
            assert input_state(store,exclude_own=False)==state
        passed('snapshot_artifact_draft_and_raw_sql_writes_are_rejected_without_changes')
        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=Permission.VIEW)
        assert quantity(read(user=sb))==110
        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=None)
        must_fail(PermissionError,lambda:read(user=sb))
        edit('configure_account',account_id=a,archived=True)
        assert quantity(read())==110
        edit('configure_account',account_id=a,archived=False)
        edit('configure_user',user_id=initial.users[1].id,enabled=False)
        must_fail(PermissionError,lambda:dashboard.choices(sb))
        passed('current_view_grants_revocation_archiving_and_disabled_user_access_rechecked')
        from uuid import UUID
        namespace='signedin-'+UUID(registry).hex+'-'+UUID(a).hex
        with gateway.transaction(sa,a) as session:
            receipt=session.artifacts.read_text('2025-01-account-publication-receipt.json')
        changed=json.loads(receipt);changed['draft_revision']=999
        def replace(text):
            with store._connection() as conn:
                conn.execute('UPDATE campaigniq_signedin_accounts_dev.artifact_storage_dev SET content=%s '
                    'WHERE namespace=%s AND artifact_key=%s',(text,namespace,'2025-01-account-publication-receipt.json'))
        replace(json.dumps(changed));corrupted=input_state(store,exclude_own=False)
        must_fail(ValueError,read)
        assert input_state(store,exclude_own=False)==corrupted
        replace(receipt)
        assert quantity(read())==110
        passed('conflicting_source_receipt_blocks_entire_dashboard_snapshot_without_repair')
        assert input_state(store,exclude_own=True)==before
        passed('existing_financial_records_real_directory_and_prior_accounts_unchanged')
        report['local_rehearsal_passed']=True
    except Exception as error:
        report.update(local_rehearsal_passed=False,error_type=type(error).__name__)
        raise
    finally:
        with (output/'account-dashboard-report.json').open('x') as f:
            json.dump(report,f,sort_keys=True,indent=2);f.write('\n')
    print('Report:',output/'account-dashboard-report.json')
    print('Only NEW synthetic local generations added. Existing accounts and production unchanged.')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only',action='store_true',required=True)
    args=parser.parse_args()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    output=Path.home()/'CampaignIQ-Records/Development-Rehearsals'/('Account-dashboard-'+stamp)
    run_rehearsal(output,os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL','dbname=campaigniq_dev'))

if __name__=='__main__':main()
