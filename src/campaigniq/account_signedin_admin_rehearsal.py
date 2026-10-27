"""Rehearse signed-in local directory administration with new synthetic records."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from campaigniq.account_administration import Identity, LegacyOwner, Permission, propose_legacy_directory
from campaigniq.account_directory_rehearsal import must_fail
from campaigniq.account_legacy_access_rehearsal import RehearsalSession, protected_rows
from campaigniq.account_signedin_administration import LocalSignedInAdministration
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory


def run_rehearsal(output, dsn='dbname=campaigniq_dev'):
    registry = str(uuid4())
    store = LocalPostgresAccountDirectory(dsn, registry, development_only=True)
    issuer = 'https://signedin-admin-rehearsal.invalid'
    a, b = Identity(issuer, registry + '-a'), Identity(issuer, registry + '-b')
    scopes = ('workspace-admin-signin-' + registry + '-a', 'workspace-admin-signin-' + registry + '-b')
    initial = propose_legacy_directory(tuple(LegacyOwner(identity, scope, 'Synthetic owner', 'Synthetic account', 'Test broker')
        for identity, scope in zip((a, b), scopes)), a)
    service = LocalSignedInAdministration(store, trusted_issuer=issuer,
        authorization={a.subject:scopes[0], b.subject:scopes[1]})
    sa, sb = RehearsalSession(a), RehearsalSession(b)
    before = protected_rows(store, ())
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = dict(format='campaigniq.signedin_administration_rehearsal', version=1, registry_id=registry,
                  production_connection_made=False, synthetic_directory_only=True, checks={})

    def passed(name):
        report['checks'][name] = True
        print(name.replace('_', ' ') + ': PASS', flush=True)

    def command(action, /, **values):
        return service.execute(sa, service.snapshot(sa).revision, action, **values)

    try:
        store.initialize()
        store.bootstrap(initial)
        prior = store._snapshot()
        must_fail(PermissionError, lambda: service.snapshot(sb))
        must_fail(PermissionError, lambda: service.snapshot(RehearsalSession(a, logged_in=False)))
        must_fail(PermissionError, lambda: service.snapshot(RehearsalSession(Identity('https://other.invalid', a.subject))))
        must_fail(PermissionError, lambda: service.execute(sb, 0, 'configure_user', user_id=initial.users[1].id, admin=True))
        assert store._snapshot() == prior
        passed('server_signin_required_and_members_cannot_read_or_edit_admin_directory')

        command('configure_user', user_id=initial.users[0].id, name='Synthetic administrator')
        current = store._snapshot()
        assert current.events[-1].actor_id == initial.users[0].id
        assert current.users[0].name == 'Synthetic administrator'
        stale = current
        must_fail(ValueError, lambda: service.execute(sa, 0, 'configure_user', user_id=initial.users[0].id, name='stale'))
        assert store._snapshot() == stale
        passed('changes_attributed_to_signed_in_admin_and_stale_forms_rejected')

        add_identity = Identity(issuer, registry + '-new-person')
        service.add_user(sa, current.revision, name='Synthetic new member', subject=add_identity.subject)
        current = store._snapshot()
        new_user = current.user(add_identity)
        assert not new_user.admin and not current.grants
        must_fail(ValueError, lambda: service.execute(sa, current.revision, 'add_user',
            identity=Identity('https://foreign.invalid', 'subject'), name='foreign'))
        command('add_account', owner_id=new_user.id, name='Synthetic second brokerage', broker='schwab')
        current = store._snapshot()
        new_account = current.accounts[-1]
        assert not new_account.legacy and new_account.storage_scope.startswith('account-')
        must_fail(PermissionError, lambda: store.authorize(a, new_account.id))
        assert [x.storage_scope for x in current.accounts[:2]] == list(scopes)
        passed('same_issuer_registration_and_new_account_metadata_preserve_existing_bindings')

        target = initial.accounts[0].id
        command('set_grant', user_id=initial.users[1].id, account_id=target, permission=Permission.VIEW)
        assert not store.authorize(b, target).writable
        command('set_grant', user_id=initial.users[1].id, account_id=target, permission=None)
        must_fail(PermissionError, lambda: store.authorize(b, target))
        command('configure_account', account_id=target, name='Synthetic renamed account', archived=True)
        assert store.authorize(a, target).storage_scope == scopes[0]
        assert not store.authorize(a, target).writable
        command('configure_account', account_id=target, archived=False)
        passed('sharing_revocation_rename_and_archive_preserve_ownership_and_history')

        prior = store._snapshot()
        append = store._append_event
        def failure(conn, event):
            append(conn, event)
            raise RuntimeError('Injected state and audit failure')
        with patch.object(store, '_append_event', side_effect=failure):
            must_fail(RuntimeError, lambda: command('configure_user', user_id=new_user.id, name='must roll back'))
        assert store._snapshot() == prior
        passed('state_and_audit_rollback_together_after_injected_failure')

        must_fail(ValueError, lambda: command('configure_user', user_id=initial.users[0].id, admin=False))
        command('configure_user', user_id=initial.users[1].id, admin=True)
        original_execute = store.execute
        def revoke_during_command(actor, expected, action, **values):
            original_execute(b, store._snapshot().revision, 'configure_user', user_id=initial.users[0].id, admin=False)
            return original_execute(actor, expected, action, **values)
        with patch.object(store, 'execute', side_effect=revoke_during_command):
            must_fail(PermissionError, lambda: service.execute(sa, store._snapshot().revision,
                'configure_user', user_id=new_user.id, name='must not save'))
        assert store._snapshot().user(add_identity).name == 'Synthetic new member'
        must_fail(PermissionError, lambda: service.snapshot(sa))
        service.execute(sb, store._snapshot().revision, 'configure_user', user_id=initial.users[0].id, admin=True)
        passed('last_admin_protected_and_permission_rechecked_under_command_lock')

        command('configure_user', user_id=initial.users[1].id, enabled=False)
        must_fail(PermissionError, lambda: service.snapshot(sb))
        assert service.snapshot(sa).user(a).admin
        command('configure_user', user_id=initial.users[1].id, enabled=True)
        assert service.snapshot(sb).user(b).enabled
        passed('disabled_admin_blocked_without_locking_out_other_owner')
        assert protected_rows(store, ()) == before
        passed('all_existing_financial_rows_and_prior_registries_unchanged')
        report['local_rehearsal_passed'] = True
    except Exception as error:
        report.update(local_rehearsal_passed=False, error_type=type(error).__name__)
        raise
    finally:
        with (output / 'signedin-administration-report.json').open('x') as f:
            json.dump(report, f, sort_keys=True, indent=2)
            f.write('\n')
    print('Report:', output / 'signedin-administration-report.json', flush=True)
    print('Only new synthetic local directory metadata added. Financial records and production unchanged.', flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only', required=True, action='store_true')
    args = parser.parse_args()
    dsn = os.environ.get('CAMPAIGNIQ_ADMIN_TEST_DATABASE_URL', 'dbname=campaigniq_dev')
    LocalPostgresAccountDirectory(dsn, str(uuid4()), development_only=args.development_only)
    output = Path.home() / 'CampaignIQ-Records/Development-Rehearsals' / (
        'Signedin-administration-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8])
    run_rehearsal(output, dsn)


if __name__ == '__main__':
    main()
