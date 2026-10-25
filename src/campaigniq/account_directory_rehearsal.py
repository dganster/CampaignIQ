"""Explicit local PostgreSQL rehearsal using synthetic admin/account records."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from unittest.mock import patch
from uuid import uuid4

from campaigniq.account_administration import Identity, LegacyOwner, Permission, propose_legacy_directory
from campaigniq.persistence.account_directory_codec import checksum
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory


def protected_tables(store):
    """Hash actual rows of all non-system tables outside this test-only schema."""
    from psycopg import sql
    with store._connection(readonly=True) as conn:
        conn.execute("SET LOCAL timezone = 'UTC'")
        tables = conn.execute('''SELECT schemaname,tablename FROM pg_tables
            WHERE schemaname NOT IN ('pg_catalog','information_schema','campaigniq_admin_dev')
            AND schemaname NOT LIKE 'pg_%' ORDER BY schemaname,tablename''').fetchall()
        result = {}
        for schema, table in tables:
            query = sql.SQL('SELECT md5(row_to_json(t)::text), count(*) FROM {}.{} AS t GROUP BY 1 ORDER BY 1').format(
                sql.Identifier(schema), sql.Identifier(table))
            rows = conn.execute(query).fetchall()
            result[schema + '.' + table] = dict(rows=sum(n for _, n in rows), checksum=checksum(rows))
        return result


def must_fail(kind, function):
    try:
        function()
    except kind:
        return
    raise AssertionError('Expected rejection: ' + kind.__name__)


def run_rehearsal(output: Path, dsn='dbname=campaigniq_dev'):
    registry_id = str(uuid4())
    store = LocalPostgresAccountDirectory(dsn, registry_id, development_only=True)
    observer = LocalPostgresAccountDirectory(dsn, registry_id, development_only=True)
    # No real identity, brokerage identifier or production record is used.
    admin = Identity('https://accounts.google.com', 'synthetic-admin')
    member = Identity('https://accounts.google.com', 'synthetic-member')
    initial = propose_legacy_directory((
        LegacyOwner(admin, 'workspace-rehearsal-admin', 'Synthetic admin', 'Synthetic account', 'Test broker'),
        LegacyOwner(member, 'workspace-rehearsal-member', 'Synthetic member', 'Synthetic account', 'Test broker'),
    ), admin)
    before = protected_tables(store)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    report = dict(format='campaigniq.admin_directory_rehearsal', version=1, database='campaigniq_dev',
                  schema='campaigniq_admin_dev', registry_id=registry_id, synthetic_records_only=True,
                  production_connection_made=False, checks={})

    def passed(name):
        report['checks'][name] = True
        print(name.replace('_', ' ') + ': PASS', flush=True)

    try:
        store.initialize()
        store.bootstrap(initial)
        store.bootstrap(initial)
        assert observer._snapshot() == initial
        passed('independent_connection_persistence')
        account, user_id = initial.accounts[0].id, initial.users[1].id
        must_fail(PermissionError, lambda: observer.authorize(member, account))
        must_fail(PermissionError, lambda: observer.authorize(admin, initial.accounts[1].id))
        passed('cross_account_isolation')

        def execute(command, **args):
            return store.execute(admin, observer._snapshot().revision, command, **args)

        execute('set_grant', user_id=user_id, account_id=account, permission=Permission.VIEW)
        assert not observer.authorize(member, account).writable
        must_fail(PermissionError, lambda: observer.authorize(member, account, write=True))
        execute('set_grant', user_id=user_id, account_id=account, permission=Permission.EDIT)
        assert observer.authorize(member, account, write=True).writable
        execute('set_grant', user_id=user_id, account_id=account, permission=None)
        must_fail(PermissionError, lambda: observer.authorize(member, account))
        passed('grant_edit_and_revocation_rechecked')

        prior = observer._snapshot()
        must_fail(PermissionError, lambda: store.execute(member, prior.revision, 'configure_account', account_id=account, archived=True))
        assert observer._snapshot() == prior
        passed('unauthorized_command_leaves_state_and_audit_unchanged')

        append = store._append_event

        def injected(conn, event):
            append(conn, event)
            raise RuntimeError('Injected failure after state and audit SQL')

        with patch.object(store, '_append_event', side_effect=injected):
            must_fail(RuntimeError, lambda: execute('configure_account', account_id=account, name='Must roll back'))
        assert observer._snapshot() == prior
        passed('injected_state_and_audit_transaction_rollback')

        barrier = threading.Barrier(2)
        revision = prior.revision

        def concurrent(name):
            client = LocalPostgresAccountDirectory(dsn, registry_id, development_only=True)
            barrier.wait(timeout=5)
            try:
                client.execute(admin, revision, 'configure_account', account_id=account, name=name)
                return 'committed'
            except ValueError:
                return 'stale'

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(concurrent, name) for name in ('Concurrent A', 'Concurrent B')]
            results = [f.result(timeout=30) for f in futures]
        assert sorted(results) == ['committed', 'stale']
        assert observer._snapshot().revision == revision + 1
        passed('concurrent_commands_prevent_lost_updates')
        must_fail(ValueError, lambda: store.execute(admin, revision, 'configure_account', account_id=account, archived=True))
        passed('stale_revision_rejected')

        execute('configure_user', user_id=user_id, enabled=False)
        must_fail(PermissionError, lambda: observer.authorize(member, initial.accounts[1].id))
        must_fail(PermissionError, lambda: observer.activity(member))
        execute('configure_user', user_id=user_id, enabled=True)
        assert observer.authorize(member, initial.accounts[1].id).writable
        passed('disable_and_reenable_preserve_ownership')

        execute('configure_account', account_id=account, archived=True)
        assert not observer.authorize(admin, account).writable
        must_fail(PermissionError, lambda: observer.authorize(admin, account, write=True))
        execute('configure_account', account_id=account, archived=False)
        assert observer.authorize(admin, account).storage_scope == 'workspace-rehearsal-admin'
        passed('archive_is_read_only_with_unchanged_storage_binding')

        prior = observer._snapshot()
        must_fail(ValueError, lambda: execute('configure_user', user_id=initial.users[0].id, enabled=False))
        assert observer._snapshot() == prior
        execute('configure_user', user_id=user_id, admin=True)
        store.execute(member, observer._snapshot().revision, 'configure_user', user_id=initial.users[0].id, enabled=False)
        must_fail(PermissionError, lambda: execute('configure_account', account_id=account, archived=True))
        store.execute(member, observer._snapshot().revision, 'configure_user', user_id=initial.users[0].id, enabled=True)
        passed('last_admin_guard_and_admin_handover')

        # Tamper only with this run's synthetic rows, then restore them exactly.
        with store._connection() as conn:
            original = conn.execute('SELECT event_sha256 FROM campaigniq_admin_dev.audit_events WHERE registry_id=%s AND sequence=1', (registry_id,)).fetchone()[0]
            conn.execute('UPDATE campaigniq_admin_dev.audit_events SET event_sha256=%s WHERE registry_id=%s AND sequence=1', ('0' * 64, registry_id))
        try:
            must_fail(ValueError, lambda: observer.authorize(admin, account))
        finally:
            with store._connection() as conn:
                conn.execute('UPDATE campaigniq_admin_dev.audit_events SET event_sha256=%s WHERE registry_id=%s AND sequence=1', (original, registry_id))
        assert observer.activity(admin) == observer._snapshot().events
        passed('audit_corruption_blocks_access')

        with store._connection() as conn:
            original = conn.execute('SELECT document_sha256 FROM campaigniq_admin_dev.directories WHERE registry_id=%s', (registry_id,)).fetchone()[0]
            conn.execute('UPDATE campaigniq_admin_dev.directories SET document_sha256=%s WHERE registry_id=%s', ('0' * 64, registry_id))
        try:
            must_fail(ValueError, lambda: observer.authorize(admin, account))
        finally:
            with store._connection() as conn:
                conn.execute('UPDATE campaigniq_admin_dev.directories SET document_sha256=%s WHERE registry_id=%s', (original, registry_id))
        assert observer._snapshot().revision > 0
        passed('snapshot_corruption_blocks_access')

        current = observer._snapshot()
        must_fail(ValueError, lambda: store.bootstrap(initial))
        other = LocalPostgresAccountDirectory(dsn, str(uuid4()), development_only=True)
        other.bootstrap(initial)
        other.execute(admin, 0, 'configure_account', account_id=account, name='Independent registry')
        assert observer._snapshot() == current
        passed('registry_partition_and_bootstrap_protection')

        after = protected_tables(store)
        assert before == after
        report.update(protected_tables_before=before, protected_tables_after=after,
                      audit_event_count=current.revision, local_rehearsal_passed=True)
        passed('all_existing_financial_tables_unchanged')
        with (output / 'admin-directory-rehearsal-report.json').open('x') as f:
            json.dump(report, f, sort_keys=True, indent=2)
            f.write('\n')
        print('Report:', output / 'admin-directory-rehearsal-report.json', flush=True)
        print('Synthetic admin records retained locally. No production connection or financial-data migration.')
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
    output = Path.home() / 'CampaignIQ-Records' / 'Development-Rehearsals' / ('Admin-directory-' + stamp)
    try:
        run_rehearsal(output, os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL', 'dbname=campaigniq_dev'))
    except Exception as error:
        parser.exit(1, 'Local rehearsal stopped (' + type(error).__name__ + '). Diagnostics, if created: ' + str(output) + '\n')


if __name__ == '__main__':
    main()
