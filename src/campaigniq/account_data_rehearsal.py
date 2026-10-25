"""Actual local PostgreSQL rehearsal for synthetic account artifacts and drafts."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from unittest.mock import patch
from uuid import UUID, uuid4

from campaigniq.account_administration import Identity, LegacyOwner, Permission, propose_legacy_directory
from campaigniq.account_directory_rehearsal import must_fail, protected_tables
from campaigniq.persistence.account_data_session import AccountTransactionFailed, ClosedAccountSession, LocalAccountDataGateway
from campaigniq.persistence.account_directory_codec import checksum
from campaigniq.persistence.import_drafts import DraftConflict
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
from campaigniq.ui.account_administration_view import DEMO_ISSUER, demo_subject_prefix


def _existing_state(store, registry_ids):
    outside = {name: value for name, value in protected_tables(store).items()
               if not name.startswith('campaigniq_accounts_dev.')}
    patterns = ['preview-' + UUID(i).hex + '-%' for i in registry_ids]
    own = {}
    statements = {
        'artifact_storage_dev': 'SELECT md5(row_to_json(t)::text),count(*) FROM campaigniq_accounts_dev.artifact_storage_dev t WHERE NOT(t.namespace LIKE ANY(%s)) GROUP BY 1 ORDER BY 1',
        'import_drafts': 'SELECT md5(row_to_json(t)::text),count(*) FROM campaigniq_accounts_dev.import_drafts t WHERE NOT(t.workspace LIKE ANY(%s)) GROUP BY 1 ORDER BY 1',
        'import_draft_documents': 'SELECT md5(row_to_json(t)::text),count(*) FROM campaigniq_accounts_dev.import_draft_documents t JOIN campaigniq_accounts_dev.import_drafts d ON d.id=t.draft_id WHERE NOT(d.workspace LIKE ANY(%s)) GROUP BY 1 ORDER BY 1',
    }
    with store._connection(readonly=True) as conn:
        conn.execute("SET LOCAL timezone='UTC'")
        for name, statement in statements.items():
            if conn.execute('SELECT to_regclass(%s)', ('campaigniq_accounts_dev.' + name,)).fetchone()[0] is None:
                rows = []
            else:
                rows = conn.execute(statement, (patterns,)).fetchall()
            own[name] = dict(rows=sum(count for _, count in rows), checksum=checksum(rows))
    return dict(existing_tables=outside, earlier_synthetic_accounts=own)


def _seed(registry_id):
    prefix = demo_subject_prefix(registry_id)
    admin = Identity(DEMO_ISSUER, prefix + 'admin')
    member = Identity(DEMO_ISSUER, prefix + 'member')
    initial = propose_legacy_directory((
        LegacyOwner(admin, 'workspace-data-test-admin', 'Synthetic admin', 'Account A', 'Test broker'),
        LegacyOwner(member, 'workspace-data-test-member', 'Synthetic member', 'Account B', 'Test broker'),
    ), admin)
    return initial, admin, member


def run_rehearsal(output, dsn='dbname=campaigniq_dev'):
    import psycopg
    registry_id, other_id = str(uuid4()), str(uuid4())
    store = LocalPostgresAccountDirectory(dsn, registry_id, development_only=True)
    other = LocalPostgresAccountDirectory(dsn, other_id, development_only=True)
    initial, admin, member = _seed(registry_id)
    before = _existing_state(store, (registry_id, other_id))
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = dict(format='campaigniq.account_data_rehearsal', version=1, database='campaigniq_dev',
                  synthetic_registry_ids=[registry_id, other_id], production_connection_made=False, checks={})

    def passed(name):
        report['checks'][name] = True
        print(name.replace('_', ' ') + ': PASS', flush=True)

    try:
        store.initialize()
        store.bootstrap(initial)
        gateway = LocalAccountDataGateway(store)
        gateway.initialize()
        a, b = (x.id for x in initial.accounts)
        key = '2025-01-synthetic-summary.json'
        with gateway.transaction(admin, a, write=True) as session:
            session.artifacts.write_text(key, '{"account":"A"}')
            draft_a = session.drafts.create_or_resume(2025, 1)
            session.drafts.upload(draft_a.id, 'broker', 'a.csv', 'text/csv', b'account,a\n')
        with gateway.transaction(member, b, write=True) as session:
            assert not session.artifacts.exists(key)
            session.artifacts.write_text(key, '{"account":"B"}')
            draft_b = session.drafts.create_or_resume(2025, 1)
            session.drafts.upload(draft_b.id, 'broker', 'b.csv', 'text/csv', b'account,b\n')
        with gateway.transaction(admin, a) as session:
            assert session.artifacts.read_text(key) == '{"account":"A"}'
            assert session.drafts.document(draft_a.id, 'broker')[2] == b'account,a\n'
        assert draft_a.id != draft_b.id and draft_a.workspace != draft_b.workspace
        passed('same_month_artifacts_and_drafts_are_account_isolated')

        with gateway.transaction(member, b, write=True) as session:
            must_fail(FileNotFoundError, lambda: session.drafts.get(draft_a.id))
            must_fail(FileNotFoundError, lambda: session.drafts.document(draft_a.id, 'broker'))
            must_fail(FileNotFoundError, lambda: session.drafts.upload(draft_a.id, 'broker', 'bad.csv', 'text/csv', b'wrong'))
            assert session.drafts.document(draft_b.id, 'broker')[2] == b'account,b\n'
        passed('foreign_draft_ids_cannot_read_or_replace_documents')

        def rollback():
            with gateway.transaction(admin, a, write=True) as session:
                session.artifacts.write_text('rollback.json', 'must roll back')
                draft = session.drafts.create_or_resume(2025, 2)
                session.drafts.upload(draft.id, 'broker', 'rollback.csv', 'text/csv', b'rollback')
                raise RuntimeError('Injected failure after artifact and document writes')
        must_fail(RuntimeError, rollback)
        with gateway.transaction(admin, a) as session:
            assert not session.artifacts.exists('rollback.json')
            assert all(d.month != 2 for d in session.drafts.list_drafts())
        passed('artifacts_drafts_and_documents_roll_back_together')

        def poisoned():
            with gateway.transaction(admin, a, write=True) as session:
                session.artifacts.write_text('before-sql-error.json', 'must roll back')
                must_fail(psycopg.Error, lambda: session.artifacts.write_text('null-content.json', None))
        must_fail(AccountTransactionFailed, poisoned)
        with gateway.transaction(admin, a) as session:
            assert not session.artifacts.exists('before-sql-error.json')
        passed('caught_sql_error_cannot_silently_commit_partial_work')

        with gateway.transaction(admin, a, write=True) as session:
            original_revision = session.drafts.get(draft_a.id).revision
            assert session.drafts.upload(draft_a.id, 'broker', 'a.csv', 'text/csv', b'account,a\n') == original_revision
            must_fail(DraftConflict, lambda: session.drafts.upload(draft_a.id, 'broker', 'new.csv', 'text/csv', b'new version'))
            session.drafts.upload(draft_a.id, 'broker', 'new.csv', 'text/csv', b'new version', replace=True)
            assert session.drafts.document(draft_a.id, 'broker')[2] == b'new version'
            versions = session._connection.execute('''SELECT content,superseded FROM campaigniq_accounts_dev.import_draft_documents
                WHERE draft_id=%s ORDER BY revision''', (draft_a.id,)).fetchall()
            assert [(bytes(row[0]), row[1]) for row in versions] == [(b'account,a\n', True), (b'new version', False)]
        passed('replacement_requires_confirmation_and_preserves_original_bytes')

        revision = store._snapshot().revision
        store.execute(admin, revision, 'add_account', owner_id=initial.users[0].id, name='Second brokerage', broker='Other broker')
        c = store._snapshot().accounts[-1].id
        with gateway.transaction(admin, c, write=True) as session:
            assert session.artifacts.list_keys() == ()
            assert session.drafts.list_drafts() == ()
            session.artifacts.write_text(key, '{"account":"C"}')
        with gateway.transaction(admin, a) as session:
            assert session.artifacts.read_text(key) == '{"account":"A"}'
        passed('multiple_brokerage_accounts_for_one_owner_are_separate')

        def edit_grant(permission):
            store.execute(admin, store._snapshot().revision, 'set_grant', user_id=initial.users[1].id,
                          account_id=a, permission=permission)
        edit_grant(Permission.VIEW)
        with gateway.transaction(member, a) as session:
            assert session.artifacts.exists(key)
            must_fail(PermissionError, lambda: session.artifacts.write_text('blocked.json', '{}'))
            must_fail(PermissionError, lambda: session.drafts.create_or_resume(2025, 3))
        def view_write():
            with gateway.transaction(member, a, write=True):
                raise AssertionError('View permission acquired write access')
        must_fail(PermissionError, view_write)
        passed('view_grants_cannot_write_artifacts_or_import_drafts')

        edit_grant(Permission.EDIT)
        # Observe a real PostgreSQL lock wait, not just thread scheduling.
        worker = LocalPostgresAccountDirectory(dsn, registry_id, development_only=True)
        connect = worker._connection
        started = threading.Event()
        worker_pid = []
        @contextmanager
        def connected(**kwargs):
            with connect(**kwargs) as conn:
                worker_pid.append(conn.info.backend_pid)
                started.set()
                yield conn
        revision = store._snapshot().revision
        with patch.object(worker, '_connection', connected), ThreadPoolExecutor(max_workers=1) as pool:
            with gateway.transaction(member, a, write=True) as session:
                future = pool.submit(worker.execute, admin, revision, 'set_grant',
                                     user_id=initial.users[1].id, account_id=a, permission=None)
                assert started.wait(timeout=5)
                deadline = time.monotonic() + 3
                blocked = False
                while time.monotonic() < deadline:
                    blocked = session._connection.execute('SELECT %s = ANY(pg_blocking_pids(%s))',
                               (session._connection.info.backend_pid, worker_pid[0])).fetchone()[0]
                    if blocked:
                        break
                    time.sleep(0.02)
                assert blocked, 'Administrative revoke did not wait for authorized account transaction'
                session.artifacts.write_text('authorized-before-revoke.json', '{}')
            future.result(timeout=10)
        def after_revoke():
            with gateway.transaction(member, a):
                raise AssertionError('Revoked permission was reused')
        must_fail(PermissionError, after_revoke)
        must_fail(ClosedAccountSession, lambda: session.artifacts.exists(key))
        passed('revocation_waits_for_active_transaction_and_blocks_later_access')

        store.execute(admin, store._snapshot().revision, 'configure_account', account_id=a, archived=True)
        def archive_write():
            with gateway.transaction(admin, a, write=True):
                raise AssertionError('Archived account acquired write access')
        must_fail(PermissionError, archive_write)
        with gateway.transaction(admin, a) as session:
            assert session.artifacts.read_text(key) == '{"account":"A"}'
        store.execute(admin, store._snapshot().revision, 'configure_account', account_id=a, archived=False)
        passed('archived_accounts_preserve_history_and_block_new_writes')

        store.execute(admin, store._snapshot().revision, 'configure_user', user_id=initial.users[1].id, enabled=False)
        def disabled():
            with gateway.transaction(member, b):
                raise AssertionError('Disabled user acquired owned account access')
        must_fail(PermissionError, disabled)
        store.execute(admin, store._snapshot().revision, 'configure_user', user_id=initial.users[1].id, enabled=True)
        passed('disabled_users_cannot_access_owned_account_data')

        other_initial, other_admin, _ = _seed(other_id)
        other.bootstrap(other_initial)
        other_gateway = LocalAccountDataGateway(other)
        # Same logical legacy account ID/workspace, different registry partition.
        assert other_initial.accounts[0].id == a
        with other_gateway.transaction(other_admin, a) as session:
            assert not session.artifacts.exists(key) and not session.drafts.list_drafts()
        passed('synthetic_registries_cannot_share_physical_storage')

        after = _existing_state(store, (registry_id, other_id))
        assert before == after
        report.update(protected_before=before, protected_after=after, local_rehearsal_passed=True)
        passed('existing_financial_tables_and_earlier_test_records_unchanged')
        with (output / 'account-data-rehearsal-report.json').open('x') as f:
            json.dump(report, f, sort_keys=True, indent=2)
            f.write('\n')
        print('Report:', output / 'account-data-rehearsal-report.json', flush=True)
        print('Only synthetic local account records were added. No publication or production connection.')
        return report
    except Exception as error:
        report.update(local_rehearsal_passed=False, error_type=type(error).__name__)
        with (output / 'failed-rehearsal.json').open('x') as f:
            json.dump(report, f, sort_keys=True, indent=2)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only', required=True, action='store_true')
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8]
    output = Path.home() / 'CampaignIQ-Records/Development-Rehearsals' / ('Account-data-' + stamp)
    try:
        run_rehearsal(output, os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL', 'dbname=campaigniq_dev'))
    except Exception as error:
        parser.exit(1, 'Local account-data rehearsal stopped (' + type(error).__name__ + '). Diagnostics, if created: ' + str(output) + '\n')


if __name__ == '__main__':
    main()
