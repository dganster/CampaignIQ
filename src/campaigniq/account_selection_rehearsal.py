"""Actual LOCAL two-account comparison, snapshot and isolation rehearsal."""
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
from campaigniq.account_reconciliation_rehearsal import ROLES
from campaigniq.account_selection_preview import SelectedDraft,compare_selected
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.account_data_session import LocalAccountDataGateway
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
from campaigniq.persistence.postgres_broker_binding import LocalBrokerBindingStore


def synthetic_sources(number,*,double=False):
    root=Path(__file__).resolve().parents[2]/'tests/fixtures'
    result={}
    for role,name in ROLES.items():
        text=(root/name).read_text().replace('12345678',number).replace('1234-5678',number[:4]+'-'+number[4:])
        if double:
            text=text.replace('+10,','+20,').replace('100.0000 100.0000 10,000.00 10,000.00','200.0000 100.0000 20,000.00 20,000.00')
            text=text.replace('110.0000 100.0000 11,000.00 11,000.00','220.0000 100.0000 22,000.00 22,000.00')
        result[role]=RestoredUpload(name,'text/csv' if name.endswith('.csv') else 'text/plain',text.encode())
    return result


def run_rehearsal(output,dsn='dbname=campaigniq_dev'):
    registry=str(uuid4());store=LocalPostgresAccountDirectory(dsn,registry,development_only=True)
    before=_existing_state(store,(registry,));initial,admin,member=_seed(registry)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    report=dict(format='campaigniq.account_selection_rehearsal',version=1,registry_id=registry,
        checks={},production_connection_made=False,publication_authorized=False)
    def passed(name):
        report['checks'][name]=True;print(name.replace('_',' ')+': PASS',flush=True)
    try:
        store.initialize();store.bootstrap(initial);gateway=LocalAccountDataGateway(store);gateway.initialize()
        service=BoundAccountImportPreviewService(gateway)
        a,foreign_account=initial.accounts[0].id,initial.accounts[1].id
        def edit(command,**args):return store.execute(admin,store._snapshot().revision,command,**args)
        edit('add_account',owner_id=initial.users[0].id,name='Second brokerage',broker='Test broker')
        b=next(account.id for account in store._snapshot().accounts if account.name=='Second brokerage')
        binder=LocalBrokerBindingStore(store)
        binder.bind(admin,a,'securities','12345678',expected_revision=store._snapshot().revision)
        binder.bind(admin,b,'securities','87654321',expected_revision=store._snapshot().revision)
        drafts={};sources={a:synthetic_sources('12345678'),b:synthetic_sources('87654321',double=True)}
        for account in (a,b):
            draft=service.create(admin,account,2025,1);revision=0
            for role,upload in sources[account].items():revision=service.save(admin,account,draft.id,revision,role,upload)
            drafts[account]=draft.id
        def selection():
            return tuple(SelectedDraft(account,drafts[account],next(d.revision for d in service.drafts(admin,account) if d.id==drafts[account]),False,False) for account in (a,b))
        def compare(identity=admin,items=None):return compare_selected(service,identity,2025,1,selection() if items is None else items,include_totals=True)
        all_before=_existing_state(store,())
        result=compare()
        assert result.all_inventory_reconciled and not result.publication_authorized
        assert [Decimal(row.closing_quantities[0][1]) for row in result.accounts]==[110,220]
        assert Decimal(result.quantity_totals[0][1])==330
        passed('two_accounts_for_one_owner_remain_separate_with_opt_in_total_330')
        assert _existing_state(store,())==all_before
        passed('comparison_leaves_all_financial_and_draft_rows_unchanged')
        def save_b(role,upload):
            rev=next(d.revision for d in service.drafts(admin,b) if d.id==drafts[b])
            return service.save(admin,b,drafts[b],rev,role,upload,replace=True)
        closing=sources[b]['schwab_brokerage_statement']
        save_b('schwab_brokerage_statement',RestoredUpload('mismatch.txt','text/plain',closing.content.replace(b'220.0000',b'221.0000')))
        result=compare()
        assert not result.all_inventory_reconciled and not result.quantity_totals
        assert result.accounts[0].inventory_reconciled and not result.accounts[1].inventory_reconciled
        passed('failed_account_is_retained_and_combined_totals_are_withheld')
        save_b('schwab_brokerage_statement',closing)
        items=selection();foreign=service.create(member,foreign_account,2025,1)
        must_fail(FileNotFoundError,lambda:compare(items=(items[0],SelectedDraft(b,foreign.id,0,False,False))))
        must_fail(ValueError,lambda:compare(items=(items[0],SelectedDraft(b,drafts[b],0,False,False))))
        must_fail(PermissionError,lambda:compare_selected(service,admin,2025,1,
            (items[0],SelectedDraft(foreign_account,foreign.id,0,False,False))))
        passed('foreign_stale_and_unauthorized_selections_return_no_partial_report')
        import campaigniq.account_selection_preview as module
        actual=module._reconcile_session;observed=[];edited=False
        history=sources[b]['thinkorswim_trade_history']
        def concurrent_source_edit(*args,**kwargs):
            nonlocal edited
            session=args[1]
            settings=session._connection.execute("SELECT current_setting('transaction_read_only'),current_setting('transaction_isolation'),pg_backend_pid()").fetchone()
            assert settings[0]=='on' and settings[1]=='repeatable read'
            observed.append(settings[2]);answer=actual(*args,**kwargs)
            if not edited:
                edited=True
                save_b('thinkorswim_trade_history',RestoredUpload('edited.csv','text/csv',history.content.replace(b'+20,',b'+30,')))
            return answer
        with patch.object(module,'_reconcile_session',side_effect=concurrent_source_edit):result=compare()
        assert result.all_inventory_reconciled and Decimal(result.quantity_totals[0][1])==330
        assert len(observed)==2 and len(set(observed))==1
        assert not compare().all_inventory_reconciled # a later snapshot sees the committed source edit
        save_b('thinkorswim_trade_history',history)
        passed('one_readonly_repeatable_snapshot_prevents_mixed_source_revisions')
        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=Permission.VIEW)
        edit('set_grant',user_id=initial.users[1].id,account_id=b,permission=Permission.VIEW)
        assert compare(identity=member).all_inventory_reconciled
        edit('set_grant',user_id=initial.users[1].id,account_id=b,permission=None)
        must_fail(PermissionError,lambda:compare(identity=member))
        edit('configure_account',account_id=b,archived=True)
        assert compare().all_inventory_reconciled
        edit('configure_account',account_id=b,archived=False)
        passed('view_access_revocation_and_archived_readonly_history_are_rechecked')
        injection_before=_existing_state(store,())
        def attempted_write(*args,**kwargs):kwargs['artifact_storage'].write_text('forbidden.json','{}')
        with patch('campaigniq.monthly_import_execution.execute_monthly_import',side_effect=attempted_write):
            must_fail(PermissionError,compare)
        assert _existing_state(store,())==injection_before
        passed('injected_artifact_write_is_rejected_without_row_changes')
        assert _existing_state(store,(registry,))==before
        passed('existing_financial_tables_and_earlier_test_records_unchanged')
        folder=Path.home()/'CampaignIQ-Records/Development-Previews'/('Account-selection-'+registry)
        folder.mkdir(parents=True,exist_ok=False,mode=0o700);config=folder/'preview-config.json'
        with os.fdopen(os.open(config,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:
            json.dump(dict(format='campaigniq.admin_preview',version=1,registry_id=registry,synthetic_only=True),stream)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        report.update(local_rehearsal_passed=True,preview_config=str(config),protected_before=before,
            protected_after=_existing_state(store,(registry,)),quantity_total='330')
        with (output/'account-selection-report.json').open('x') as stream:json.dump(report,stream,sort_keys=True,indent=2)
        print('Report:',output/'account-selection-report.json',flush=True)
        print('Ready two-account preview configuration:',config,flush=True)
        print('Only synthetic local sources and metadata added. No production connection or publication.')
        return report
    except Exception as error:
        report.update(local_rehearsal_passed=False,error_type=type(error).__name__)
        with (output/'failed-rehearsal.json').open('x') as stream:json.dump(report,stream,sort_keys=True,indent=2)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--development-only',required=True,action='store_true');parser.parse_args()
    output=Path.home()/'CampaignIQ-Records/Development-Rehearsals'/('Account-selection-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8])
    try:run_rehearsal(output,os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL','dbname=campaigniq_dev'))
    except Exception as error:parser.exit(1,'Local selection rehearsal stopped ('+type(error).__name__+'). Diagnostics, if created: '+str(output)+'\n')


if __name__=='__main__':main()
