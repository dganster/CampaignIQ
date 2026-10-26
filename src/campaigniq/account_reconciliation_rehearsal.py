"""Run a complete synthetic month on guarded LOCAL PostgreSQL, without writes from preview."""
import argparse
from datetime import datetime,timezone
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
from campaigniq.account_reconciliation_preview import reconcile_saved
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.account_data_session import LocalAccountDataGateway
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
from campaigniq.persistence.postgres_broker_binding import LocalBrokerBindingStore

ROLES={'thinkorswim_trade_history':'account-month-history.csv','schwab_brokerage_statement':'account-month-closing.txt',
       'schwab_opening_position_snapshot':'account-month-opening.txt','schwab_realized_gain_loss':'account-month-realized.txt'}


def run_rehearsal(output,dsn='dbname=campaigniq_dev'):
    registry=str(uuid4())
    store=LocalPostgresAccountDirectory(dsn,registry,development_only=True)
    before=_existing_state(store,(registry,))
    initial,admin,member=_seed(registry)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    report=dict(format='campaigniq.account_reconciliation_rehearsal',version=1,registry_id=registry,
        checks={},production_connection_made=False,publication_authorized=False)
    def passed(name):
        report['checks'][name]=True
        print(name.replace('_',' ')+': PASS',flush=True)
    try:
        fixtures=Path(__file__).resolve().parents[2]/'tests/fixtures'
        store.initialize();store.bootstrap(initial)
        gateway=LocalAccountDataGateway(store);gateway.initialize()
        service=BoundAccountImportPreviewService(gateway)
        a,b=initial.accounts[0].id,initial.accounts[1].id
        LocalBrokerBindingStore(store).bind(admin,a,'securities','12345678',expected_revision=0)
        draft=service.create(admin,a,2025,1);revision=0
        for role,name in ROLES.items():
            revision=service.save(admin,a,draft.id,revision,role,
                RestoredUpload(name,'text/csv' if name.endswith('.csv') else 'text/plain',(fixtures/name).read_bytes()))
        def preview(identity=admin,target=a,draft_id=draft.id,rev=None):
            return reconcile_saved(service,identity,target,draft_id,revision if rev is None else rev,
                forex_applicable=False,crypto_applicable=False)
        all_before=_existing_state(store,())
        result=preview()
        assert result.inventory_reconciled and not result.publication_authorized
        assert len(result.closing_quantities)==1 and Decimal(result.closing_quantities[0][1])==110
        passed('complete_bound_month_replays_to_110_shares_and_reconciles')
        assert _existing_state(store,())==all_before
        passed('reconciliation_preview_leaves_all_financial_and_draft_rows_unchanged')
        revision=service.save(admin,a,draft.id,revision,'schwab_brokerage_statement',
            RestoredUpload('mismatch.txt','text/plain',(fixtures/'account-month-closing-mismatch.txt').read_bytes()),replace=True)
        mismatch_before=_existing_state(store,())
        result=preview()
        assert not result.inventory_reconciled and len(result.mismatches)==1
        assert Decimal(result.mismatches[0].difference)==-1
        assert _existing_state(store,())==mismatch_before
        passed('independent_closing_difference_is_reported_without_repair_or_override')
        foreign=service.create(member,b,2025,1)
        must_fail(FileNotFoundError,lambda:preview(draft_id=foreign.id,rev=0))
        must_fail(ValueError,lambda:preview(rev=0))
        passed('foreign_draft_and_stale_revision_cannot_reconcile')
        # Restore the correct synthetic statement as a new preserved document version.
        revision=service.save(admin,a,draft.id,revision,'schwab_brokerage_statement',
            RestoredUpload('closing.txt','text/plain',(fixtures/'account-month-closing.txt').read_bytes()),replace=True)
        def edit(command,**args):return store.execute(admin,store._snapshot().revision,command,**args)
        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=Permission.VIEW)
        assert preview(identity=member).inventory_reconciled
        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=None)
        must_fail(PermissionError,lambda:preview(identity=member))
        passed('view_grants_allow_readonly_reconciliation_and_revocation_blocks_access')
        injection_before=_existing_state(store,())
        def attempted_write(*args,**kwargs):kwargs['artifact_storage'].write_text('forbidden.json','{}')
        with patch('campaigniq.monthly_import_execution.execute_monthly_import',side_effect=attempted_write):
            must_fail(PermissionError,preview)
        assert _existing_state(store,())==injection_before
        passed('injected_artifact_write_is_rejected_and_all_rows_remain_unchanged')
        assert _existing_state(store,(registry,))==before
        passed('existing_financial_tables_and_earlier_test_records_unchanged')
        config_folder=Path.home()/'CampaignIQ-Records/Development-Previews'/('Reconciliation-'+registry)
        config_folder.mkdir(parents=True,exist_ok=False,mode=0o700)
        config=config_folder/'preview-config.json'
        with os.fdopen(os.open(config,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:
            json.dump(dict(format='campaigniq.admin_preview',version=1,registry_id=registry,synthetic_only=True),stream)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        report.update(local_rehearsal_passed=True,preview_config=str(config),protected_before=before,
                      protected_after=_existing_state(store,(registry,)),draft_revision=revision)
        with (output/'account-reconciliation-report.json').open('x') as stream:
            json.dump(report,stream,sort_keys=True,indent=2);stream.write('\n')
        print('Report:',output/'account-reconciliation-report.json',flush=True)
        print('Ready synthetic preview configuration:',config,flush=True)
        print('Only synthetic local source drafts and metadata added. Nothing published; no production connection.')
        return report
    except Exception as error:
        report.update(local_rehearsal_passed=False,error_type=type(error).__name__)
        with (output/'failed-rehearsal.json').open('x') as stream:json.dump(report,stream,sort_keys=True,indent=2)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only',required=True,action='store_true');parser.parse_args()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    output=Path.home()/'CampaignIQ-Records/Development-Rehearsals'/('Account-reconciliation-'+stamp)
    try:run_rehearsal(output,os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL','dbname=campaigniq_dev'))
    except Exception as error:
        parser.exit(1,'Local reconciliation rehearsal stopped ('+type(error).__name__+'). Diagnostics, if created: '+str(output)+'\n')


if __name__=='__main__':main()
