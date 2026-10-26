"""Synthetic local PostgreSQL broker-binding and wrong-account rejection rehearsal."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from campaigniq.account_data_rehearsal import _existing_state,_seed
from campaigniq.account_directory_rehearsal import must_fail
from campaigniq.account_import_preview import BoundAccountImportPreviewService
from campaigniq.broker_account_binding import bindings
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.account_data_session import LocalAccountDataGateway
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
from campaigniq.persistence.postgres_broker_binding import LocalBrokerBindingStore


def run_rehearsal(output,dsn='dbname=campaigniq_dev'):
    registry=str(uuid4())
    store=LocalPostgresAccountDirectory(dsn,registry,development_only=True)
    initial,admin,member=_seed(registry)
    before=_existing_state(store,(registry,))
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    report=dict(format='campaigniq.broker_binding_rehearsal',version=1,registry_id=registry,
        checks={},production_connection_made=False,publication_authorized=False)
    def passed(name):
        report['checks'][name]=True
        print(name.replace('_',' ')+': PASS',flush=True)
    try:
        store.initialize();store.bootstrap(initial)
        gateway=LocalAccountDataGateway(store);gateway.initialize()
        service=BoundAccountImportPreviewService(gateway)
        binder=LocalBrokerBindingStore(store)
        a,b=initial.accounts[0].id,initial.accounts[1].id
        def set_binding(account,kind,number,**kwargs):
            return binder.bind(admin,account,kind,number,expected_revision=store._snapshot().revision,**kwargs)
        set_binding(a,'securities','12345678')
        directory=store._snapshot()
        assert directory.revision==1 and bindings(directory,a)['securities'].suffix=='5678'
        assert '12345678' not in repr(directory.events)
        assert set_binding(a,'securities','1234-5678')==1
        passed('audited_admin_binding_is_idempotent_and_displays_only_suffix')
        must_fail(ValueError,lambda:set_binding(b,'securities','12345678'))
        assert store._snapshot()==directory
        passed('same_broker_account_cannot_bind_to_two_accounts')
        must_fail(PermissionError,lambda:binder.bind(member,b,'securities','87654321',expected_revision=1))
        must_fail(ValueError,lambda:binder.bind(admin,b,'securities','87654321',expected_revision=0))
        assert store._snapshot()==directory
        passed('unauthorized_and_stale_binding_commands_leave_state_and_audit_unchanged')
        d=service.create(admin,a,2025,1)
        role='thinkorswim_trade_history'
        matching=RestoredUpload('synthetic.csv','text/csv',b'Account Statement for 12345678 since 1/1/25 through 1/31/25')
        wrong=RestoredUpload('wrong.csv','text/csv',b'Account Statement for 87654321 since 1/1/25 through 1/31/25')
        must_fail(ValueError,lambda:service.save(admin,a,d.id,0,role,wrong))
        with gateway.transaction(admin,a) as session:
            assert session.drafts.get(d.id).revision==0
            must_fail(FileNotFoundError,lambda:session.drafts.document(d.id,role))
        assert service.save(admin,a,d.id,0,role,matching)==1
        must_fail(ValueError,lambda:service.save(admin,a,d.id,1,role,wrong,replace=True))
        with gateway.transaction(admin,a) as session:
            assert session.drafts.document(d.id,role)[2]==matching.content
            assert session.drafts.get(d.id).revision==1
        result=service.preview(admin,a,d.id,1)
        assert any(v.role=='account:'+role and v.valid for v in result.validations)
        assert not result.inputs_ready and not result.publication_authorized
        passed('wrong_account_upload_and_replacement_rejected_without_draft_changes')
        must_fail(ValueError,lambda:set_binding(a,'securities','87654321'))
        set_binding(a,'securities','87654321',confirm_replace=True)
        result=service.preview(admin,a,d.id,1)
        assert any(v.role=='account:'+role and not v.valid for v in result.validations)
        assert not result.inputs_ready
        passed('binding_changes_preserve_history_and_invalidate_old_document_identity')
        fxrole='schwab_forex_transaction_report'
        fx=RestoredUpload('forex.csv','text/csv',b'Account (USD): 7654321, Base currency: USD')
        must_fail(ValueError,lambda:service.save(admin,a,d.id,1,fxrole,fx))
        set_binding(a,'forex','7654321')
        assert service.save(admin,a,d.id,1,fxrole,fx)==2
        passed('forex_binding_is_separate_from_securities_binding')
        baseline=store._snapshot()
        with patch.object(store,'_append_event',side_effect=RuntimeError('injected audit failure')):
            must_fail(RuntimeError,lambda:set_binding(b,'securities','11223344'))
        assert store._snapshot()==baseline
        passed('binding_and_audit_roll_back_together_on_injected_failure')
        after=_existing_state(store,(registry,))
        assert before==after
        passed('existing_financial_tables_and_earlier_test_records_unchanged')
        report.update(local_rehearsal_passed=True,protected_before=before,protected_after=after)
        with (output/'broker-binding-report.json').open('x') as stream:
            json.dump(report,stream,sort_keys=True,indent=2);stream.write('\n')
        print('Report:',output/'broker-binding-report.json',flush=True)
        print('Only synthetic local bindings and drafts added. No production connection or publication.')
        return report
    except Exception as error:
        report.update(local_rehearsal_passed=False,error_type=type(error).__name__)
        with (output/'failed-rehearsal.json').open('x') as stream:json.dump(report,stream,sort_keys=True,indent=2)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only',required=True,action='store_true')
    parser.parse_args()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    output=Path.home()/'CampaignIQ-Records/Development-Rehearsals'/('Broker-binding-'+stamp)
    try:run_rehearsal(output,os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL','dbname=campaigniq_dev'))
    except Exception as error:
        parser.exit(1,'Local binding rehearsal stopped ('+type(error).__name__+'). Diagnostics, if created: '+str(output)+'\n')


if __name__=='__main__':main()
