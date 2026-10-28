"""LOCAL synthetic rehearsal of real-sign-in publication with atomic rollback."""
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
    issuer = 'https://signedin-publication-rehearsal.invalid'
    admin, member = Identity(issuer,registry+'-a'), Identity(issuer,registry+'-b')
    scopes = ('workspace-publish-'+registry+'-a','workspace-publish-'+registry+'-b')
    initial = propose_legacy_directory(tuple(LegacyOwner(identity,scope,'Synthetic owner','Retained account','schwab')
        for identity,scope in zip((admin,member),scopes)),admin)
    gateway = LocalSignedInInputGateway(store,trusted_issuer=issuer,
        authorization={admin.subject:scopes[0],member.subject:scopes[1]})
    service = SignedInMonthlyInputs(gateway)
    sa,sb = RehearsalSession(admin),RehearsalSession(member)
    before = input_state(store,exclude_own=True)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    report = dict(format='campaigniq.signedin_publication_rehearsal',version=1,registry_id=registry,
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
        def publish(user=sa,target=a,identifier=da.id,rev=None,authorize=True):
            return service.publish(user,target,identifier,revision if rev is None else rev,
                authorize_local_publication=authorize,forex_applicable=False,crypto_applicable=False)
        state=input_state(store,exclude_own=False)
        must_fail(PermissionError,lambda:publish(authorize=False))
        must_fail(ValueError,lambda:publish(rev=0))
        must_fail(FileNotFoundError,lambda:publish(target=b))
        must_fail(FileNotFoundError,lambda:publish(identifier=db.id,rev=0))
        must_fail(PermissionError,lambda:publish(user=sb))
        must_fail(PermissionError,lambda:publish(user=admin))
        must_fail(PermissionError,lambda:publish(user=RehearsalSession(Identity('https://other.invalid',admin.subject))))
        must_fail(ValueError,lambda:publish(target=initial.accounts[0].id))
        assert input_state(store,exclude_own=False)==state
        passed('confirmation_current_revision_real_signin_and_new_account_scope_required')

        gateway.bind(sa,a,'securities','11223344',expected_revision=store._snapshot().revision,confirm_replace=True)
        state=input_state(store,exclude_own=False)
        must_fail(ValueError,publish)
        assert input_state(store,exclude_own=False)==state
        gateway.bind(sa,a,'securities','12345678',expected_revision=store._snapshot().revision,confirm_replace=True)
        passed('current_broker_identity_rechecked_before_any_generation')

        closing=sources['schwab_brokerage_statement']
        revision=service.save(sa,a,da.id,revision,'schwab_brokerage_statement',
            RestoredUpload('mismatch.txt','text/plain',closing.content.replace(b'110.0000',b'111.0000')),replace=True)
        state=input_state(store,exclude_own=False)
        must_fail(ValueError,publish)
        assert input_state(store,exclude_own=False)==state
        revision=service.save(sa,a,da.id,revision,'schwab_brokerage_statement',closing,replace=True)
        passed('closing_mismatch_rejects_publication_without_partial_artifacts')

        with gateway.transaction(sa,a) as session:
            original_documents={role:session.drafts.document(da.id,role) for role in sources}
        from campaigniq.persistence.account_data_session import _Artifacts
        actual_write=_Artifacts.write_text
        state=input_state(store,exclude_own=False)
        def fail_after_marker(storage,key,content):
            actual_write(storage,key,content)
            if key=='2025-01-finalized.json':raise RuntimeError('injected post-marker failure')
        with patch.object(_Artifacts,'write_text',fail_after_marker):must_fail(RuntimeError,publish)
        assert input_state(store,exclude_own=False)==state
        passed('injected_post_marker_failure_rolls_back_every_user_table')
        def caught_sql_error(storage,key,content):
            try:storage._session._connection.execute('SELECT 1/0')
            except psycopg.errors.DivisionByZero:pass
            return actual_write(storage,key,content)
        from campaigniq.persistence.account_data_session import AccountTransactionFailed
        with patch.object(_Artifacts,'write_text',caught_sql_error):must_fail(AccountTransactionFailed,publish)
        assert input_state(store,exclude_own=False)==state
        passed('caught_sql_error_cannot_commit_a_partial_generation')

        from concurrent.futures import ThreadPoolExecutor
        import threading
        barrier=threading.Barrier(2)
        def compete():
            barrier.wait(timeout=10)
            try:return publish()
            except ValueError as error:
                if 'already authoritative' not in str(error):raise
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:compete(),range(2)))
        assert sum(r is not None for r in results)==1
        passed('concurrent_duplicate_publication_has_exactly_one_winner')

        from campaigniq.persistence.authoritative_lot_state import load_preceding_authoritative_state_from_storage
        from datetime import date
        with gateway.transaction(sa,a) as session:
            january={key:session.artifacts.read_text(key) for key in session.artifacts.list_keys()}
            receipt=json.loads(session.artifacts.read_text('2025-01-account-publication-receipt.json'))
            assert receipt['draft_revision']==revision and receipt['actor_id']==initial.users[0].id
            assert receipt['format']=='campaigniq.signedin_local_account_publication'
            assert len(receipt['original_documents'])==4
            assert session.drafts.get(da.id).revision==revision
            assert {role:session.drafts.document(da.id,role) for role in sources}==original_documents
            preceding=load_preceding_authoritative_state_from_storage(session.artifacts,period_start=date(2025,2,1))
            assert sum(l.quantity for lots in preceding[2]._lots.values() for l in lots)==110
        assert service.published(sa,a,da.id,revision)
        passed('committed_generation_and_source_receipt_are_readable_independently')

        state=input_state(store,exclude_own=False)
        must_fail(ValueError,publish)
        must_fail(ValueError,lambda:service.save(sa,a,da.id,revision,'schwab_brokerage_statement',closing,replace=True))
        assert input_state(store,exclude_own=False)==state
        with gateway.transaction(sa,b) as session:assert session.artifacts.list_keys()==()
        passed('published_month_and_source_documents_preserved_and_other_accounts_isolated')

        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=Permission.VIEW)
        assert service.published(sb,a,da.id,revision)
        must_fail(PermissionError,lambda:publish(user=sb))
        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=None)
        must_fail(PermissionError,lambda:service.published(sb,a,da.id,revision))
        edit('configure_account',account_id=a,archived=True)
        assert service.published(sa,a,da.id,revision)
        must_fail(PermissionError,publish)
        edit('configure_account',account_id=a,archived=False)
        edit('configure_user',user_id=initial.users[1].id,enabled=False)
        must_fail(PermissionError,lambda:publish(user=sb))
        passed('view_revocation_archiving_and_disabled_user_permissions_rechecked')

        from campaigniq.account_publication_rehearsal import february_sources
        feb=service.create(sa,a,2025,2);febrev=0
        for role,upload in february_sources().items():febrev=service.save(sa,a,feb.id,febrev,role,upload)
        publish(identifier=feb.id,rev=febrev)
        with gateway.transaction(sa,a) as session:
            from campaigniq.persistence.lot_book_store import load_lot_book_from_storage
            state=load_lot_book_from_storage(session.artifacts,'2025-02-lot-book.json')
            assert sum(l.quantity for lots in state.lot_book._lots.values() for l in lots)==115
            assert all(session.artifacts.read_text(key)==value for key,value in january.items())
            try:session.artifacts.write_text('blocked.json','{}')
            except PermissionError:pass
            else:raise AssertionError('Ordinary gateway acquired artifact writer')
        passed('next_month_uses_published_opening_and_ordinary_gateway_stays_readonly')

        earlier=service.create(sa,a,2024,12);gap=service.create(sa,a,2025,4)
        state=input_state(store,exclude_own=False)
        must_fail(ValueError,lambda:publish(identifier=earlier.id,rev=0))
        must_fail(ValueError,lambda:publish(identifier=gap.id,rev=0))
        assert input_state(store,exclude_own=False)==state
        passed('historical_backfill_and_missing_preceding_month_remain_blocked')
        assert input_state(store,exclude_own=True)==before
        passed('all_existing_financial_records_real_directory_and_prior_accounts_unchanged')
        report['local_rehearsal_passed']=True
    except Exception as error:
        report.update(local_rehearsal_passed=False,error_type=type(error).__name__)
        raise
    finally:
        with (output/'signedin-publication-report.json').open('x') as f:
            json.dump(report,f,sort_keys=True,indent=2);f.write('\n')
    print('Report:',output/'signedin-publication-report.json',flush=True)
    print('January and February published only inside a NEW synthetic local account. Production unchanged.',flush=True)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--development-only',required=True,action='store_true')
    args=p.parse_args()
    dsn=os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL','dbname=campaigniq_dev')
    LocalPostgresAccountDirectory(dsn,str(uuid4()),development_only=args.development_only)
    output=Path.home()/'CampaignIQ-Records/Development-Rehearsals'/(
        'Signedin-publication-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8])
    run_rehearsal(output,dsn)


if __name__=='__main__':main()
