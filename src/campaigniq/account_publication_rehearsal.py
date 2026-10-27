"""Publish synthetic LOCAL account months with rollback and source preservation checks."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import threading
from datetime import date,datetime,timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from campaigniq.account_administration import Permission
from campaigniq.account_data_rehearsal import _existing_state,_seed
from campaigniq.account_directory_rehearsal import must_fail
from campaigniq.account_import_preview import BoundAccountImportPreviewService
from campaigniq.account_local_publication import publish_synthetic_month
from campaigniq.account_reconciliation_rehearsal import ROLES
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.account_data_session import LocalAccountDataGateway,_Artifacts
from campaigniq.persistence.authoritative_lot_state import load_preceding_authoritative_state_from_storage
from campaigniq.persistence.lot_book_store import load_lot_book_from_storage
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
from campaigniq.persistence.postgres_broker_binding import LocalBrokerBindingStore


def february_sources():
    root=Path(__file__).resolve().parents[2]/'tests/fixtures'
    result={}
    for role,name in ROLES.items():
        if role=='schwab_opening_position_snapshot':continue
        text=(root/name).read_text().replace('since 1/1/25 through 1/31/25','since 2/1/25 through 2/28/25')
        text=text.replace('01/15/25','02/15/25').replace('+10,','+5,')
        text=text.replace('January 1-31, 2025','February 1-28, 2025').replace('01/01/2025 to 01/31/2025','02/01/2025 to 02/28/2025')
        text=text.replace('110.0000 100.0000 11,000.00 11,000.00','115.0000 100.0000 11,500.00 11,500.00')
        result[role]=RestoredUpload(name,'text/csv' if name.endswith('.csv') else 'text/plain',text.encode())
    return result


def run_rehearsal(output,dsn='dbname=campaigniq_dev'):
    registry=str(uuid4());store=LocalPostgresAccountDirectory(dsn,registry,development_only=True)
    before=_existing_state(store,(registry,));initial,admin,member=_seed(registry)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    report=dict(format='campaigniq.local_account_publication_rehearsal',version=1,registry_id=registry,
        checks={},production_connection_made=False,production_publication_authorized=False)
    def passed(name):
        report['checks'][name]=True;print(name.replace('_',' ')+': PASS',flush=True)
    try:
        store.initialize();store.bootstrap(initial);gateway=LocalAccountDataGateway(store);gateway.initialize()
        service=BoundAccountImportPreviewService(gateway);a,b=initial.accounts[0].id,initial.accounts[1].id
        binder=LocalBrokerBindingStore(store)
        binder.bind(admin,a,'securities','12345678',expected_revision=0)
        binder.bind(admin,b,'securities','87654321',expected_revision=1)
        root=Path(__file__).resolve().parents[2]/'tests/fixtures'
        draft=service.create(admin,a,2025,1);revision=0
        for role,name in ROLES.items():
            revision=service.save(admin,a,draft.id,revision,role,
                RestoredUpload(name,'text/csv' if name.endswith('.csv') else 'text/plain',(root/name).read_bytes()))
        def publish(identity=admin,account=a,draft_id=draft.id,rev=None):
            return publish_synthetic_month(service,identity,account,draft_id,revision if rev is None else rev,
                authorize_synthetic_publication=True,forex_applicable=False,crypto_applicable=False)
        def original_docs():
            with gateway.transaction(admin,a) as session:
                return {role:session.drafts.document(draft.id,role) for role in ROLES}
        originals=original_docs()
        all_before=_existing_state(store,())
        must_fail(PermissionError,lambda:publish_synthetic_month(service,admin,a,draft.id,revision,
            forex_applicable=False,crypto_applicable=False))
        must_fail(ValueError,lambda:publish(rev=0))
        assert _existing_state(store,())==all_before
        passed('explicit_local_authorization_and_current_source_revision_required')
        # Fail after the visibility marker was staged; no partial generation may commit.
        actual_write=_Artifacts.write_text
        def fail_after_marker(storage,key,content):
            actual_write(storage,key,content)
            if key=='2025-01-finalized.json':raise RuntimeError('injected post-marker failure')
        with patch.object(_Artifacts,'write_text',fail_after_marker):must_fail(RuntimeError,publish)
        assert _existing_state(store,())==all_before
        passed('injected_failure_after_publication_marker_rolls_back_every_row')
        start=threading.Barrier(2)
        def competing_attempt():
            start.wait(timeout=10)
            try:return publish()
            except ValueError as error:
                if 'already authoritative' not in str(error):raise
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts=list(pool.map(lambda _:competing_attempt(),range(2)))
        assert sum(item is not None for item in attempts)==1
        result=next(item for item in attempts if item is not None)
        passed('concurrent_duplicate_publication_has_one_winner_without_overwrite')
        assert result.local_published and not result.production_publication_authorized
        with gateway.transaction(admin,a) as session:
            assert len(result.artifact_keys)==10
            january={key:session.artifacts.read_text(key) for key in session.artifacts.list_keys()}
            receipt=json.loads(session.artifacts.read_text('2025-01-account-publication-receipt.json'))
            assert receipt['draft_revision']==revision and receipt['account_id']==a
            assert set(receipt['original_documents'])==set(ROLES)
            state=load_preceding_authoritative_state_from_storage(session.artifacts,period_start=date(2025,2,1))
            assert state is not None and sum(l.quantity for lots in state[2]._lots.values() for l in lots)==110
        assert original_docs()==originals
        passed('local_generation_commits_with_source_receipt_and_preserved_original_documents')
        duplicate_before=_existing_state(store,())
        must_fail(ValueError,publish)
        assert _existing_state(store,())==duplicate_before
        passed('published_month_replacement_is_rejected_without_changes')
        # Other account cannot inherit this generation; role grants still apply.
        with gateway.transaction(member,b) as session:assert session.artifacts.list_keys()==()
        store.execute(admin,store._snapshot().revision,'set_grant',user_id=initial.users[1].id,account_id=a,permission=Permission.VIEW)
        must_fail(PermissionError,lambda:publish(identity=member))
        passed('other_accounts_are_isolated_and_view_grants_cannot_publish')
        feb=service.create(admin,a,2025,2);febrev=0
        for role,upload in february_sources().items():febrev=service.save(admin,a,feb.id,febrev,role,upload)
        publish(draft_id=feb.id,rev=febrev)
        with gateway.transaction(admin,a) as session:
            febstate=load_lot_book_from_storage(session.artifacts,'2025-02-lot-book.json')
            assert sum(l.quantity for lots in febstate.lot_book._lots.values() for l in lots)==115
            assert all(session.artifacts.read_text(key)==value for key,value in january.items())
        passed('next_month_uses_published_opening_state_and_preserves_prior_generation')
        march=service.create(admin,a,2025,3);marchrev=0
        for role,upload in february_sources().items():
            text=upload.content.decode().replace('2/1/25 through 2/28/25','3/1/25 through 3/31/25').replace('02/15/25','03/15/25')
            text=text.replace('February 1-28, 2025','March 1-31, 2025').replace('02/01/2025 to 02/28/2025','03/01/2025 to 03/31/2025')
            # Deliberately retain the February closing quantity: 115 + 5 != 115.
            marchrev=service.save(admin,a,march.id,marchrev,role,RestoredUpload(upload.name,upload.media_type,text.encode()))
        mismatch_before=_existing_state(store,())
        must_fail(ValueError,lambda:publish(draft_id=march.id,rev=marchrev))
        assert _existing_state(store,())==mismatch_before
        passed('closing_mismatch_cannot_publish_or_change_existing_generations')
        earlier=service.create(admin,a,2024,12)
        gap=service.create(admin,a,2025,4)
        guard_before=_existing_state(store,())
        must_fail(ValueError,lambda:publish(draft_id=earlier.id,rev=0))
        must_fail(ValueError,lambda:publish(draft_id=gap.id,rev=0))
        assert _existing_state(store,())==guard_before
        passed('historical_backfill_and_missing_predecessor_are_blocked')
        assert _existing_state(store,(registry,))==before
        passed('existing_financial_tables_and_earlier_test_records_unchanged')
        report.update(local_rehearsal_passed=True,local_published_months=['2025-01','2025-02'],
            protected_before=before,protected_after=_existing_state(store,(registry,)))
        with (output/'local-publication-report.json').open('x') as stream:json.dump(report,stream,sort_keys=True,indent=2)
        print('Report:',output/'local-publication-report.json',flush=True)
        print('January and February published only inside the NEW synthetic local account. No production connection or publication.')
        return report
    except Exception as error:
        report.update(local_rehearsal_passed=False,error_type=type(error).__name__)
        with (output/'failed-rehearsal.json').open('x') as stream:json.dump(report,stream,sort_keys=True,indent=2)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--development-only',required=True,action='store_true');parser.parse_args()
    output=Path.home()/'CampaignIQ-Records/Development-Rehearsals'/('Local-account-publication-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8])
    try:run_rehearsal(output,os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL','dbname=campaigniq_dev'))
    except Exception as error:parser.exit(1,'Local account publication rehearsal stopped ('+type(error).__name__+'). Diagnostics, if created: '+str(output)+'\n')


if __name__=='__main__':main()
