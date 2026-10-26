"""Local SQL rehearsal for account-scoped monthly input previews."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from campaigniq.account_administration import Permission
from campaigniq.account_data_rehearsal import _existing_state, _seed
from campaigniq.account_directory_rehearsal import must_fail
from campaigniq.account_import_preview import AccountImportPreviewService
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.account_data_session import LocalAccountDataGateway
from campaigniq.persistence.import_drafts import DraftConflict
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory


def run_rehearsal(output, dsn='dbname=campaigniq_dev'):
    registry = str(uuid4())
    store = LocalPostgresAccountDirectory(dsn, registry, development_only=True)
    initial, admin, member = _seed(registry)
    before = _existing_state(store, (registry,))
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = dict(format='campaigniq.account_input_preview_rehearsal', version=1,
        registry_id=registry, checks={}, production_connection_made=False, publication_authorized=False)
    def passed(name):
        report['checks'][name] = True
        print(name.replace('_', ' ') + ': PASS', flush=True)
    def edit(command, **args):
        return store.execute(admin,store._snapshot().revision,command,**args)
    try:
        store.initialize()
        store.bootstrap(initial)
        gateway = LocalAccountDataGateway(store)
        gateway.initialize()
        service = AccountImportPreviewService(gateway)
        a, b = initial.accounts[0].id, initial.accounts[1].id
        draft = service.create(admin,a,2025,1)
        foreign = service.create(member,b,2025,1)
        assert draft.id != foreign.id
        assert service.drafts(admin,a) == (draft,)
        must_fail(FileNotFoundError,lambda:service.preview(admin,a,foreign.id,0))
        passed('same_month_preview_and_foreign_draft_isolation')
        preview = service.preview(admin,a,draft.id,0)
        assert not preview.inputs_ready and not preview.publication_authorized and not preview.inventory_reconciled
        assert any(v.role == 'opening_state' and not v.valid for v in preview.validations)
        passed('existing_input_validator_reports_missing_evidence_without_publication')
        role='thinkorswim_trade_history'
        original=RestoredUpload('synthetic.csv','text/csv',b'invalid synthetic trade history')
        revision=service.save(admin,a,draft.id,0,role,original)
        assert revision == 1
        preview=service.preview(admin,a,draft.id,revision)
        assert not preview.inputs_ready
        must_fail(ValueError,lambda:service.preview(admin,a,draft.id,0))
        must_fail(ValueError,lambda:service.save(admin,a,draft.id,0,role,original))
        passed('saved_documents_validated_and_stale_revisions_rejected')
        replacement=RestoredUpload('second.csv','text/csv',b'second invalid synthetic history')
        must_fail(DraftConflict,lambda:service.save(admin,a,draft.id,1,role,replacement))
        assert service.save(admin,a,draft.id,1,role,replacement,replace=True) == 2
        with gateway.transaction(admin,a) as session:
            assert session.drafts.document(draft.id,role)[2] == replacement.content
            with session._operation() as conn:
                old=conn.execute('SELECT content FROM import_draft_documents WHERE draft_id=%s AND superseded=true',
                    (draft.id,)).fetchall()
            assert old == [(original.content,)]
        passed('replacement_confirmation_preserves_original_bytes')
        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=Permission.VIEW)
        assert service.preview(member,a,draft.id,2).revision == 2
        must_fail(PermissionError,lambda:service.create(member,a,2025,2))
        must_fail(PermissionError,lambda:service.save(member,a,draft.id,2,role,original,replace=True))
        edit('set_grant',user_id=initial.users[1].id,account_id=a,permission=None)
        must_fail(PermissionError,lambda:service.preview(member,a,draft.id,2))
        passed('view_grants_and_revocation_rechecked_by_preview_service')
        edit('configure_account',account_id=a,archived=True)
        assert service.preview(admin,a,draft.id,2).revision == 2
        must_fail(PermissionError,lambda:service.save(admin,a,draft.id,2,role,original,replace=True))
        edit('configure_user',user_id=initial.users[1].id,enabled=False)
        must_fail(PermissionError,lambda:service.preview(member,b,foreign.id,0))
        passed('archived_accounts_remain_readable_and_disabled_users_are_blocked')
        after=_existing_state(store,(registry,))
        assert before == after
        passed('existing_financial_tables_and_earlier_synthetic_records_unchanged')
        report.update(local_rehearsal_passed=True,protected_before=before,protected_after=after)
        with (output/'account-input-preview-report.json').open('x') as stream:
            json.dump(report,stream,sort_keys=True,indent=2)
            stream.write('\n')
        print('Report:',output/'account-input-preview-report.json',flush=True)
        print('Synthetic local drafts retained. No production connection, reconciliation or publication.')
        return report
    except Exception as error:
        report.update(local_rehearsal_passed=False,error_type=type(error).__name__)
        with (output/'failed-rehearsal.json').open('x') as stream:
            json.dump(report,stream,sort_keys=True,indent=2)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only',required=True,action='store_true')
    parser.parse_args()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    output=Path.home()/'CampaignIQ-Records/Development-Rehearsals'/('Account-input-preview-'+stamp)
    try:
        run_rehearsal(output,os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL','dbname=campaigniq_dev'))
    except Exception as error:
        parser.exit(1,'Local preview rehearsal stopped ('+type(error).__name__+'). Diagnostics, if created: '+str(output)+'\n')


if __name__=='__main__':
    main()
