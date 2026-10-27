"""New synthetic LOCAL accounts exercise the real-sign-in monthly input path."""
import argparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Event
from unittest.mock import patch
from uuid import UUID, uuid4

from campaigniq.account_administration import Identity, LegacyOwner, Permission, propose_legacy_directory
from campaigniq.account_directory_rehearsal import must_fail
from campaigniq.account_legacy_access_rehearsal import RehearsalSession, protected_rows
from campaigniq.account_selection_rehearsal import synthetic_sources
from campaigniq.account_signedin_inputs import SignedInMonthlyInputs
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.account_data_session import AccountTransactionFailed
from campaigniq.persistence.account_directory_codec import checksum
from campaigniq.persistence.import_drafts import DraftConflict
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
from campaigniq.persistence.signedin_account_data import DATA_SCHEMA, LocalSignedInInputGateway


def input_state(store, *, exclude_own):
    result = {k:v for k,v in protected_rows(store, (), exclude_registry=exclude_own).items()
              if not k.startswith(DATA_SCHEMA + '.')}
    prefix = 'signedin-' + UUID(store.registry_id).hex + '-%'
    with store._connection(readonly=True) as conn:
        conn.execute("SET LOCAL timezone='UTC'")
        for table, clause in (
            ('artifact_storage_dev', 't.namespace'), ('import_drafts', 't.workspace'),
            ('import_draft_documents', 'd.workspace')):
            if conn.execute('SELECT to_regclass(%s)', (DATA_SCHEMA + '.' + table,)).fetchone()[0] is None:
                continue
            # Schema and table names are fixed constants, never browser input.
            source = DATA_SCHEMA + '.' + table + ' t'
            if table == 'import_draft_documents':
                source += ' JOIN ' + DATA_SCHEMA + '.import_drafts d ON d.id=t.draft_id'
            where = ' WHERE ' + clause + ' NOT LIKE %s' if exclude_own else ''
            query = 'SELECT md5(row_to_json(t)::text),count(*) FROM ' + source + where + ' GROUP BY 1 ORDER BY 1'
            rows = conn.execute(query, (prefix,) if exclude_own else None).fetchall()
            if rows:
                result[DATA_SCHEMA + '.' + table] = checksum(rows)
    return result


def run_rehearsal(output, dsn='dbname=campaigniq_dev'):
    import psycopg
    registry = str(uuid4())
    store = LocalPostgresAccountDirectory(dsn, registry, development_only=True)
    issuer = 'https://signedin-input-rehearsal.invalid'
    admin, member = Identity(issuer, registry + '-admin'), Identity(issuer, registry + '-member')
    scopes = ('workspace-input-' + registry + '-a', 'workspace-input-' + registry + '-b')
    initial = propose_legacy_directory(tuple(LegacyOwner(identity, scope, 'Synthetic owner', 'Retained account', 'schwab')
        for identity, scope in zip((admin, member), scopes)), admin)
    gateway = LocalSignedInInputGateway(store, trusted_issuer=issuer,
        authorization={admin.subject:scopes[0], member.subject:scopes[1]})
    service = SignedInMonthlyInputs(gateway)
    sa, sb = RehearsalSession(admin), RehearsalSession(member)
    before = input_state(store, exclude_own=True)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = dict(format='campaigniq.signedin_inputs_rehearsal', version=1, registry_id=registry,
                  production_connection_made=False, publication_authorized=False, checks={})

    def passed(name):
        report['checks'][name] = True
        print(name.replace('_', ' ') + ': PASS', flush=True)

    def edit(action, /, **values):
        return store.execute(admin, store._snapshot().revision, action, **values)

    def binding(account, number, **values):
        return gateway.bind(sa, account, 'securities', number,
                            expected_revision=store._snapshot().revision, **values)

    try:
        store.initialize()
        store.bootstrap(initial)
        for name, owner in (('New A', initial.users[0].id), ('New B', initial.users[0].id), ('Member account', initial.users[1].id)):
            edit('add_account', owner_id=owner, name=name, broker='schwab')
        a, b, c = (account.id for account in store._snapshot().accounts if not account.legacy)
        must_fail(PermissionError, lambda: gateway.initialize(sb))
        gateway.initialize(sa)
        assert [x.id for x in gateway.overview(sa).accounts] == [a, b]
        must_fail(ValueError, lambda: service.create(sa, initial.accounts[0].id, 2025, 1))
        must_fail(PermissionError, lambda: service.create(admin, a, 2025, 1))
        must_fail(PermissionError, lambda: service.create(sa, c, 2025, 1))
        da, db = service.create(sa, a, 2025, 1), service.create(sa, b, 2025, 1)
        assert da.id != db.id and da.workspace != db.workspace
        with gateway.transaction(sa, a) as session:
            assert session.drafts.workspace.startswith('signedin-' + UUID(registry).hex)
            assert session._connection.execute('SELECT current_schema()').fetchone()[0] == DATA_SCHEMA
        passed('real_session_new_account_isolation_and_legacy_upload_rejection')

        binding(a, '12345678')
        prior = store._snapshot()
        must_fail(ValueError, lambda: binding(b, '12345678'))
        must_fail(PermissionError, lambda: gateway.bind(sb, c, 'securities', '11223344', expected_revision=prior.revision))
        assert store._snapshot() == prior
        binding(b, '87654321')
        assert gateway.overview(sa).accounts[0].suffixes == (('securities', '5678'),)
        sources = synthetic_sources('12345678')
        role = 'thinkorswim_trade_history'
        must_fail(ValueError, lambda: service.save(sa, a, da.id, 0, role, synthetic_sources('87654321')[role]))
        assert service.drafts(sa, a)[0].revision == 0
        passed('audited_binding_duplicate_identifier_and_wrong_account_upload_guards')

        original = sources[role]
        revision = service.save(sa, a, da.id, 0, role, original)
        changed = RestoredUpload('replacement.csv', 'text/csv', original.content + b'\n')
        must_fail(DraftConflict, lambda: service.save(sa, a, da.id, revision, role, changed))
        must_fail(ValueError, lambda: service.save(sa, a, da.id, 0, role, changed, replace=True))
        must_fail(FileNotFoundError, lambda: service.save(sa, b, da.id, revision, role, original))
        revision = service.save(sa, a, da.id, revision, role, changed, replace=True)
        with gateway.transaction(sa, a) as session:
            assert session.drafts.document(da.id, role)[2] == changed.content
            rows = session._connection.execute('SELECT content FROM import_draft_documents WHERE draft_id=%s AND superseded=true', (da.id,)).fetchall()
            assert rows == [(original.content,)]
        passed('stale_and_foreign_drafts_blocked_and_replaced_original_bytes_preserved')

        for other_role, upload in sources.items():
            if other_role != role:
                revision = service.save(sa, a, da.id, revision, other_role, upload)
        all_before = input_state(store, exclude_own=False)
        result = service.preview(sa, a, da.id, revision, forex_applicable=False, crypto_applicable=False)
        assert result.inputs_ready and not result.inventory_reconciled and not result.publication_authorized
        assert input_state(store, exclude_own=False) == all_before
        passed('existing_input_checks_pass_without_reconciliation_publication_or_row_changes')

        def rollback_upload():
            with gateway.transaction(sa, a, write=True) as session:
                session.drafts.upload(da.id, 'rollback-test', 'test.csv', 'text/csv', b'synthetic rollback')
                raise RuntimeError('Injected draft transaction failure')
        must_fail(RuntimeError, rollback_upload)
        def caught_sql_error():
            with gateway.transaction(sa, a, write=True) as session:
                session.drafts.upload(da.id, 'rollback-test', 'test.csv', 'text/csv', b'synthetic rollback')
                must_fail(psycopg.errors.DivisionByZero, lambda: session._connection.execute('SELECT 1/0'))
        must_fail(AccountTransactionFailed, caught_sql_error)
        assert input_state(store, exclude_own=False) == all_before
        passed('draft_document_and_revision_rollback_including_caught_sql_errors')

        with gateway.transaction(sa, a, write=True) as session:
            must_fail(PermissionError, lambda: session.artifacts.write_text('blocked.json', '{}'))
            must_fail(PermissionError, lambda: session.artifacts.delete('anything.json'))
        def attempted_write(*args, **kwargs):
            kwargs['artifact_storage'].write_text('blocked.json', '{}')
        with patch('campaigniq.account_import_preview.prepare_monthly_import', side_effect=attempted_write):
            must_fail(PermissionError, lambda: service.preview(sa, a, da.id, revision))
        assert input_state(store, exclude_own=False) == all_before
        passed('financial_artifact_writes_blocked_even_inside_upload_or_preview')

        edit('set_grant', user_id=initial.users[1].id, account_id=a, permission=Permission.VIEW)
        assert service.preview(sb, a, da.id, revision, forex_applicable=False, crypto_applicable=False).inputs_ready
        must_fail(PermissionError, lambda: service.save(sb, a, da.id, revision, role, original, replace=True))
        started = Event()
        def revoke():
            started.set()
            edit('set_grant', user_id=initial.users[1].id, account_id=a, permission=None)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with gateway.transaction(sb, a) as session:
                future = pool.submit(revoke)
                assert started.wait(timeout=3)
                must_fail(TimeoutError, lambda: future.result(timeout=0.2))
                assert session.drafts.get(da.id).revision == revision
            future.result(timeout=10)
        must_fail(PermissionError, lambda: service.drafts(sb, a))
        passed('view_access_cannot_upload_and_revocation_waits_for_active_transaction')

        edit('configure_account', account_id=a, archived=True)
        assert service.preview(sa, a, da.id, revision, forex_applicable=False, crypto_applicable=False).inputs_ready
        must_fail(PermissionError, lambda: service.create(sa, a, 2025, 2))
        edit('configure_account', account_id=a, archived=False)
        edit('configure_user', user_id=initial.users[1].id, enabled=False)
        must_fail(PermissionError, lambda: gateway.overview(sb))
        assert gateway.overview(sa).accounts
        edit('configure_user', user_id=initial.users[1].id, enabled=True)
        binding(a, '11223344', confirm_replace=True)
        assert not service.preview(sa, a, da.id, revision, forex_applicable=False, crypto_applicable=False).inputs_ready
        with gateway.transaction(sa, a) as session:
            assert session.drafts.document(da.id, role)[2] == changed.content
        binding(a, '12345678', confirm_replace=True)
        passed('archived_history_disabled_users_and_changed_binding_rechecked')

        prior = store._snapshot()
        append = store._append_event
        def failed_binding(conn, event):
            append(conn, event)
            raise RuntimeError('Injected binding ledger failure')
        with patch.object(store, '_append_event', side_effect=failed_binding):
            must_fail(RuntimeError, lambda: binding(a, '11223344', confirm_replace=True))
        assert store._snapshot() == prior
        passed('broker_binding_and_audit_rollback_together')
        assert input_state(store, exclude_own=True) == before
        passed('all_existing_financial_rows_real_directory_and_earlier_test_accounts_unchanged')
        report['local_rehearsal_passed'] = True
    except Exception as error:
        report.update(local_rehearsal_passed=False, error_type=type(error).__name__)
        raise
    finally:
        with (output / 'signedin-inputs-report.json').open('x') as f:
            json.dump(report, f, sort_keys=True, indent=2)
            f.write('\n')
    print('Report:', output / 'signedin-inputs-report.json', flush=True)
    print('Only new synthetic local account drafts, sources and directory metadata added. No production connection or publication.', flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only', required=True, action='store_true')
    args = parser.parse_args()
    dsn = os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL', 'dbname=campaigniq_dev')
    LocalPostgresAccountDirectory(dsn, str(uuid4()), development_only=args.development_only)
    output = Path.home() / 'CampaignIQ-Records/Development-Rehearsals' / (
        'Signedin-inputs-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8])
    run_rehearsal(output, dsn)


if __name__ == '__main__':
    main()
