"""Seed NEW synthetic local legacy workspaces and verify the signed-in reader.

Preserves all prior rows. No real OIDC callback or production connection runs.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4

from campaigniq.account_administration import Identity, LegacyOwner, Permission, propose_legacy_directory
from campaigniq.account_directory_rehearsal import must_fail
from campaigniq.account_legacy_access import LocalLegacyAccountAccess
from campaigniq.persistence.account_directory_codec import checksum
from campaigniq.persistence.import_drafts import SCHEMA
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory


class RehearsalSession:
    """Explicit fake server session for service tests, never used by the UI."""
    def __init__(self, identity, *, logged_in=True):
        self.identity, self.logged_in = identity, logged_in

    def to_dict(self):
        return dict(iss=self.identity.issuer, sub=self.identity.subject, is_logged_in=self.logged_in)


def protected_rows(store, scopes, *, exclude_registry=True):
    from psycopg import sql
    result = {}
    with store._connection(readonly=True) as conn:
        conn.execute("SET LOCAL timezone='UTC'")
        tables = conn.execute('''SELECT schemaname,tablename FROM pg_tables
            WHERE schemaname NOT IN ('pg_catalog','information_schema')
            AND schemaname NOT LIKE 'pg_%' ORDER BY 1,2''').fetchall()
        for schema, table in tables:
            source = sql.SQL('{}.{} t').format(sql.Identifier(schema), sql.Identifier(table))
            params = ()
            condition = sql.SQL('')
            if exclude_registry and schema == 'campaigniq_admin_dev' and table in ('directories', 'audit_events'):
                condition, params = sql.SQL(' WHERE t.registry_id != %s'), (store.registry_id,)
            elif schema == 'public' and table in ('artifact_storage_dev', 'import_drafts'):
                column = 'namespace' if table == 'artifact_storage_dev' else 'workspace'
                condition = sql.SQL(' WHERE NOT(t.{} = ANY(%s))').format(sql.Identifier(column))
                params = (list(scopes),)
            elif schema == 'public' and table == 'import_draft_documents':
                source += sql.SQL(' JOIN public.import_drafts d ON d.id=t.draft_id')
                condition, params = sql.SQL(' WHERE NOT(d.workspace = ANY(%s))'), (list(scopes),)
            query = sql.SQL('SELECT md5(row_to_json(t)::text),count(*) FROM ') + source + condition + sql.SQL(' GROUP BY 1 ORDER BY 1')
            rows = conn.execute(query, params).fetchall()
            # New empty tables do not represent changes to prior rows.
            if rows:
                result[schema + '.' + table] = checksum(rows)
    return result


def seed_sources(store, scopes):
    with store._connection() as conn:
        # CREATE statements in the reused legacy schema target public. The
        # actual read-only service uses pg_catalog first and performs no DDL.
        conn.execute('SET LOCAL search_path=public,pg_catalog')
        conn.execute('''CREATE TABLE IF NOT EXISTS public.artifact_storage_dev (
            namespace text NOT NULL, artifact_key text NOT NULL, content text NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(namespace,artifact_key))''')
        for statement in SCHEMA.split(';'):
            if statement.strip():
                conn.execute(statement)
        for n, scope in enumerate(scopes):
            conn.execute('INSERT INTO public.artifact_storage_dev(namespace,artifact_key,content) VALUES(%s,%s,%s)',
                         (scope, '2025-01-synthetic.json', json.dumps(dict(shares=110 * (n + 1)))))
            for abandoned in (False, True):
                identifier = uuid4()
                conn.execute('''INSERT INTO public.import_drafts(id,workspace,year,month,state,revision)
                    VALUES(%s,%s,2025,%s,%s,2)''',
                    (identifier, scope, 2 if abandoned else 1, 'abandoned' if abandoned else 'draft'))
                for revision in (1, 2):
                    raw = ('synthetic original ' + str(n) + ':' + str(revision)).encode()
                    conn.execute('''INSERT INTO public.import_draft_documents
                        (id,draft_id,role,filename,media_type,sha256,content,revision,superseded)
                        VALUES(%s,%s,'broker','test.csv','text/csv',%s,%s,%s,%s)''',
                        (uuid4(), identifier, sha256(raw).hexdigest(), raw, revision, revision == 1))


def run_rehearsal(output, dsn='dbname=campaigniq_dev'):
    import psycopg
    registry = str(uuid4())
    store = LocalPostgresAccountDirectory(dsn, registry, development_only=True)
    issuer = 'https://signin-rehearsal.invalid'
    admin, member = (Identity(issuer, 'synthetic-' + registry + suffix) for suffix in ('-admin', '-member'))
    scopes = ('workspace-signin-' + registry + '-a', 'workspace-signin-' + registry + '-b')
    initial = propose_legacy_directory(tuple(LegacyOwner(identity, scope, 'Synthetic owner',
        'Synthetic legacy account', 'Test broker') for identity, scope in zip((admin, member), scopes)), admin)
    mapping = dict(zip((admin.subject, member.subject), scopes))
    reader = LocalLegacyAccountAccess(store, trusted_issuer=issuer, authorization=mapping)
    before = protected_rows(store, scopes)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = dict(format='campaigniq.legacy_account_access_rehearsal', version=1, registry_id=registry,
        production_connection_made=False, oidc_provider_callback_tested=False, checks={})

    def passed(name):
        report['checks'][name] = True
        print(name.replace('_', ' ') + ': PASS', flush=True)

    try:
        store.initialize()
        store.bootstrap(initial)
        seed_sources(store, scopes)
        a, b = (account.id for account in initial.accounts)
        sa, sb = RehearsalSession(admin), RehearsalSession(member)
        assert [c.id for c in reader.choices(sa)] == [a]
        assert [c.id for c in reader.choices(sb)] == [b]
        must_fail(PermissionError, lambda: reader.inventory(sa, b))
        must_fail(PermissionError, lambda: reader.inventory(sb, a))
        passed('signed_in_owner_access_isolated_and_admin_has_no_implicit_financial_access')
        first = reader.inventory(sa, a)
        second = reader.inventory(sb, b)
        assert first.storage_scope == scopes[0] and second.storage_scope == scopes[1]
        assert first.artifact_sha256 != second.artifact_sha256
        assert first.adapter_parity_verified and first.artifact_count == 1
        assert first.active_drafts == first.abandoned_drafts == 1 and first.document_versions == 4
        passed('retained_namespaces_artifacts_drafts_and_all_original_versions_match')
        state = protected_rows(store, (), exclude_registry=False)
        assert reader.inventory(sa, a) == first
        assert protected_rows(store, (), exclude_registry=False) == state
        passed('read_operations_leave_every_user_table_unchanged')

        with reader._session(sa, a) as (conn, session, _):
            assert conn.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
            assert conn.execute('SHOW transaction_isolation').fetchone()[0] == 'repeatable read'
            must_fail(PermissionError, lambda: session.artifacts.write_text('blocked.json', '{}'))
            must_fail(PermissionError, lambda: session.drafts.create_or_resume(2025, 3))
        def injected_sql():
            with reader._session(sa, a) as (conn, _, _):
                must_fail(psycopg.errors.ReadOnlySqlTransaction, lambda: conn.execute(
                    'UPDATE public.artifact_storage_dev SET content=content WHERE namespace=%s', (scopes[0],)))
                # Even a caught SQL error must force session rollback on exit.
        from campaigniq.persistence.account_data_session import AccountTransactionFailed, ClosedAccountSession
        must_fail(AccountTransactionFailed, injected_sql)
        must_fail(ClosedAccountSession, lambda: session.artifacts.list_keys())
        assert protected_rows(store, (), exclude_registry=False) == state
        passed('python_and_postgres_readonly_guards_and_expired_handles')

        def execute(command, **values):
            store.execute(admin, store._snapshot().revision, command, **values)
        execute('set_grant', user_id=initial.users[1].id, account_id=a, permission=Permission.VIEW)
        assert reader.inventory(sb, a).artifact_sha256 == first.artifact_sha256
        execute('set_grant', user_id=initial.users[1].id, account_id=a, permission=None)
        must_fail(PermissionError, lambda: reader.inventory(sb, a))
        execute('configure_user', user_id=initial.users[1].id, enabled=False)
        must_fail(PermissionError, lambda: reader.choices(sb))
        assert reader.inventory(sa, a).artifact_sha256 == first.artifact_sha256
        execute('configure_user', user_id=initial.users[1].id, enabled=True)
        execute('configure_account', account_id=b, archived=True)
        assert reader.inventory(sb, b).artifact_sha256 == second.artifact_sha256
        passed('grants_revocation_disabled_users_and_archived_history_rechecked')
        wrong = RehearsalSession(Identity('https://other-rehearsal.invalid', admin.subject))
        must_fail(PermissionError, lambda: reader.choices(wrong))
        must_fail(PermissionError, lambda: reader.choices(RehearsalSession(admin, logged_in=False)))
        bad = LocalLegacyAccountAccess(store, trusted_issuer=issuer,
            authorization={admin.subject: scopes[1], member.subject: scopes[0]})
        must_fail(ValueError, lambda: bad.inventory(sa, a))
        passed('wrong_issuer_logged_out_and_changed_legacy_mapping_fail_closed')

        with store._connection() as conn:
            doc = conn.execute('''SELECT d.id,d.sha256 FROM public.import_draft_documents d
                JOIN public.import_drafts p ON p.id=d.draft_id WHERE p.workspace=%s LIMIT 1''', (scopes[0],)).fetchone()
            conn.execute('UPDATE public.import_draft_documents SET sha256=%s WHERE id=%s', ('0' * 64, doc[0]))
        try:
            must_fail(ValueError, lambda: reader.inventory(sa, a))
        finally:
            with store._connection() as conn:
                conn.execute('UPDATE public.import_draft_documents SET sha256=%s WHERE id=%s', (doc[1], doc[0]))
        passed('corrupt_original_document_blocks_parity_without_repair')
        after = protected_rows(store, scopes)
        assert before == after
        passed('all_existing_financial_tables_and_earlier_test_records_unchanged')
        report.update(local_rehearsal_passed=True, inventory=asdict(first), protected_before=before, protected_after=after)
    except Exception as error:
        report.update(local_rehearsal_passed=False, error_type=type(error).__name__)
        raise
    finally:
        with (output / 'legacy-account-access-report.json').open('x') as f:
            json.dump(report, f, sort_keys=True, indent=2)
            f.write('\n')
    print('Report:', output / 'legacy-account-access-report.json', flush=True)
    print('Only new synthetic local sources and directory metadata added. No real sign-in or production connection.', flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only', required=True, action='store_true')
    args = parser.parse_args()
    output = Path.home() / 'CampaignIQ-Records' / 'Development-Rehearsals' / (
        'Legacy-account-access-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8])
    # Constructor rechecks explicit local mode and routing before any operation.
    LocalPostgresAccountDirectory(os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL', 'dbname=campaigniq_dev'),
                                   str(uuid4()), development_only=args.development_only)
    run_rehearsal(output, os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL', 'dbname=campaigniq_dev'))


if __name__ == '__main__':
    main()
