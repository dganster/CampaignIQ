"""LOCAL synthetic rehearsal of real-sign-in read-only closing reconciliation."""
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
    from campaigniq import account_signedin_reconciliation as module
    registry = str(uuid4())
    store = LocalPostgresAccountDirectory(dsn, registry, development_only=True)
    issuer = 'https://signedin-reconciliation-rehearsal.invalid'
    admin, member = Identity(issuer,registry+'-a'), Identity(issuer,registry+'-b')
    scopes = ('workspace-reconcile-'+registry+'-a','workspace-reconcile-'+registry+'-b')
    initial = propose_legacy_directory(tuple(LegacyOwner(identity,scope,'Synthetic owner','Retained account','schwab')
        for identity,scope in zip((admin,member),scopes)),admin)
    gateway = LocalSignedInInputGateway(store,trusted_issuer=issuer,
        authorization={admin.subject:scopes[0],member.subject:scopes[1]})
    service = SignedInMonthlyInputs(gateway)
    sa,sb = RehearsalSession(admin),RehearsalSession(member)
    before = input_state(store,exclude_own=True)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    report = dict(format='campaigniq.signedin_reconciliation_rehearsal',version=1,registry_id=registry,
        checks={},production_connection_made=False,publication_authorized=False)
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
        def replay(user=sa,target=a,identifier=da.id,rev=None):
            return service.reconcile(user,target,identifier,revision if rev is None else rev,
                forex_applicable=False,crypto_applicable=False)
        actual=module._reconcile_session; observed=[]
        def checked(*args,**kwargs):
            session=args[1]
            observed.append((session._connection.execute('SHOW transaction_read_only').fetchone()[0],
                session._connection.execute('SHOW transaction_isolation').fetchone()[0],
                session._connection.execute('SELECT current_schema()').fetchone()[0]))
            return actual(*args,**kwargs)
        state=input_state(store,exclude_own=False)
        with patch.object(module,'_reconcile_session',side_effect=checked):result=replay()
        assert result.inventory_reconciled and not result.publication_authorized
        assert len(result.closing_quantities)==1 and Decimal(result.closing_quantities[0][1])==110
        assert observed==[('on','repeatable read',DATA_SCHEMA)]
        passed('complete_bound_month_reconciles_to_110_shares_in_readonly_snapshot')
        assert input_state(store,exclude_own=False)==state
        passed('reconciliation_leaves_every_user_table_and_source_revision_unchanged')

        closing=sources['schwab_brokerage_statement']
        revision=service.save(sa,a,da.id,revision,'schwab_brokerage_statement',
            RestoredUpload('mismatch.txt','text/plain',closing.content.replace(b'110.0000',b'111.0000')),replace=True)
        state=input_state(store,exclude_own=False)
        result=replay()
        assert not result.inventory_reconciled and len(result.mismatches)==1
        assert Decimal(result.mismatches[0].difference)==-1
        assert input_state(store,exclude_own=False)==state
        revision=service.save(sa,a,da.id,revision,'schwab_brokerage_statement',closing,replace=True)
        passed('independent_closing_difference_reported_without_repair_or_override')
        must_fail(FileNotFoundError,lambda:replay(target=b))
        must_fail(FileNotFoundError,lambda:replay(identifier=db.id,rev=0))
        must_fail(ValueError,lambda:replay(rev=0))
        must_fail(PermissionError,lambda:replay(user=sb))
        must_fail(PermissionError,lambda:replay(user=RehearsalSession(Identity('https://other.invalid',admin.subject))))
        passed('foreign_drafts_stale_revisions_and_wrong_identities_cannot_reconcile')

        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=Permission.VIEW)
        assert replay(user=sb).inventory_reconciled
        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=None)
        must_fail(PermissionError,lambda:replay(user=sb))
        edit('configure_account',account_id=a,archived=True)
        assert replay().inventory_reconciled
        edit('configure_account',account_id=a,archived=False)
        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=Permission.VIEW)
        edit('configure_user',user_id=initial.users[1].id,enabled=False)
        must_fail(PermissionError,lambda:replay(user=sb))
        passed('view_access_revocation_archive_and_disabled_user_rechecked')

        state=input_state(store,exclude_own=False)
        def artifact_write(*args,**kwargs):args[1].artifacts.write_text('blocked.json','{}')
        def draft_write(*args,**kwargs):args[1].drafts.create_or_resume(2025,2)
        def sql_write(*args,**kwargs):
            args[1]._connection.execute('UPDATE campaigniq_signedin_accounts_dev.import_drafts SET revision=revision WHERE id=%s',(da.id,))
        for callback,kind in ((artifact_write,PermissionError),(draft_write,PermissionError),
                              (sql_write,psycopg.errors.ReadOnlySqlTransaction)):
            with patch.object(module,'_reconcile_session',side_effect=callback):must_fail(kind,replay)
            assert input_state(store,exclude_own=False)==state
        passed('injected_artifact_draft_and_raw_sql_writes_rejected_without_row_changes')
        gateway.bind(sa,a,'securities','11223344',expected_revision=store._snapshot().revision,confirm_replace=True)
        must_fail(ValueError,replay)
        gateway.bind(sa,a,'securities','12345678',expected_revision=store._snapshot().revision,confirm_replace=True)
        assert replay().inventory_reconciled
        passed('current_broker_binding_rechecked_and_original_documents_preserved')
        assert input_state(store,exclude_own=True)==before
        passed('all_existing_financial_records_real_directory_and_prior_test_accounts_unchanged')
        report['local_rehearsal_passed']=True
    except Exception as error:
        report.update(local_rehearsal_passed=False,error_type=type(error).__name__)
        raise
    finally:
        with (output/'signedin-reconciliation-report.json').open('x') as f:
            json.dump(report,f,sort_keys=True,indent=2);f.write('\n')
    print('Report:',output/'signedin-reconciliation-report.json',flush=True)
    print('Only NEW synthetic local sources added. No production connection or publication.',flush=True)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--development-only',required=True,action='store_true')
    args=p.parse_args()
    dsn=os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL','dbname=campaigniq_dev')
    LocalPostgresAccountDirectory(dsn,str(uuid4()),development_only=args.development_only)
    output=Path.home()/'CampaignIQ-Records/Development-Rehearsals'/(
        'Signedin-reconciliation-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8])
    run_rehearsal(output,dsn)


if __name__=='__main__':main()
