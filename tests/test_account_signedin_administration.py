from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from campaigniq.account_administration import Identity, LegacyOwner, propose_legacy_directory
from campaigniq.account_legacy_access_rehearsal import RehearsalSession
from campaigniq.account_signedin_administration import LocalSignedInAdministration
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory

ISSUER = 'https://accounts.example.test'
A, B = Identity(ISSUER, 'subject-a'), Identity(ISSUER, 'subject-b')


@pytest.fixture
def harness():
    initial = propose_legacy_directory((LegacyOwner(A, 'workspace-a', 'A', 'Account A', 'schwab'),
        LegacyOwner(B, 'workspace-b', 'B', 'Account B', 'schwab')), A)
    current = [initial]
    store = object.__new__(LocalPostgresAccountDirectory)
    store.registry_id = str(uuid4())
    store._snapshot = lambda: current[0]
    def execute(actor, revision, command, **arguments):
        current[0] = getattr(current[0], command)(actor, expected_revision=revision, **arguments)
        return current[0].revision
    store.execute = Mock(side_effect=execute)
    service = LocalSignedInAdministration(store, trusted_issuer=ISSUER,
        authorization={A.subject:'workspace-a', B.subject:'workspace-b'})
    return SimpleNamespace(initial=initial, current=current, store=store, service=service,
        user=RehearsalSession(A), member=RehearsalSession(B))


def test_signedin_actor_is_selected_server_side_and_audited(harness):
    h = harness
    h.service.execute(h.user, 0, 'configure_user', user_id=h.initial.users[0].id, name='Renamed')
    assert h.store.execute.call_args.args[0] == A
    assert h.current[0].events[-1].actor_id == h.initial.users[0].id
    assert h.current[0].users[0].name == 'Renamed'


@pytest.mark.parametrize('which', ['member', 'loggedout', 'foreign', 'rawclaims'])
def test_untrusted_or_unauthorized_actor_cannot_read_directory_or_issue_command(harness, which):
    h = harness
    user = {'member':h.member, 'loggedout':RehearsalSession(A, logged_in=False),
        'foreign':RehearsalSession(Identity('https://other.test', A.subject)),
        'rawclaims':dict(iss=ISSUER, sub=A.subject, is_logged_in=True)}[which]
    with pytest.raises(PermissionError): h.service.snapshot(user)
    with pytest.raises(PermissionError): h.service.execute(user, 0, 'configure_user', user_id=h.initial.users[1].id, admin=True)
    h.store.execute.assert_not_called()


def test_stale_revision_rejected_without_audit_or_state_change(harness):
    h = harness
    h.service.execute(h.user, 0, 'configure_user', user_id=h.initial.users[0].id, name='Saved')
    prior = h.current[0]
    with pytest.raises(ValueError):
        h.service.execute(h.user, 0, 'configure_user', user_id=h.initial.users[0].id, name='Stale')
    assert h.current[0] == prior


def test_registration_pins_issuer_and_never_uses_email_to_find_existing_owner(harness):
    h = harness
    h.service.add_user(h.user, 0, name='New member', subject='new-exact-subject')
    added = h.current[0].user(Identity(ISSUER, 'new-exact-subject'))
    assert not added.admin and added.enabled
    assert not h.current[0].grants and h.current[0].accounts == h.initial.accounts
    with pytest.raises(ValueError):
        h.service.execute(h.user, 1, 'add_user', name='Other', identity=Identity('https://other.test', 'new'))
    assert h.current[0].revision == 1


def test_duplicate_subject_and_noncanonical_subject_rejected(harness):
    h = harness
    for subject in (A.subject, ' new ', ''):
        with pytest.raises(ValueError): h.service.add_user(h.user, 0, name='Invalid', subject=subject)
    assert h.current[0] == h.initial


def test_admin_revocation_between_snapshot_and_command_rechecked(harness):
    h = harness
    h.current[0] = h.initial.configure_user(A, h.initial.users[1].id, admin=True, expected_revision=0)
    original = h.store.execute.side_effect
    def race(actor, revision, command, **arguments):
        h.current[0] = h.current[0].configure_user(B, h.initial.users[0].id, admin=False,
                                                expected_revision=h.current[0].revision)
        return original(actor, revision, command, **arguments)
    h.store.execute.side_effect = race
    with pytest.raises(PermissionError):
        h.service.execute(h.user, 1, 'configure_user', user_id=h.initial.users[1].id, name='Must not save')
    assert h.current[0].users[1].name == 'B'


def test_no_raw_database_or_financial_interface(harness):
    h = harness
    h.service.snapshot(h.user)
    h.store.execute.assert_not_called()
    assert not hasattr(h.service, 'artifacts') and not hasattr(h.service, 'publish')


def test_corrupt_audit_or_changed_legacy_mapping_blocks_admin_commands(harness):
    h = harness
    h.store._snapshot = Mock(side_effect=ValueError('audit differs'))
    with pytest.raises(ValueError): h.service.execute(h.user, 0, 'configure_user', user_id=h.initial.users[0].id, name='X')
    h.store.execute.assert_not_called()


def test_other_disabled_owner_does_not_block_enabled_admin(harness):
    h = harness
    h.current[0] = h.initial.configure_user(A, h.initial.users[1].id, enabled=False, expected_revision=0)
    assert h.service.snapshot(h.user) == h.current[0]
    with pytest.raises(PermissionError): h.service.snapshot(h.member)


def test_last_admin_cannot_disable_self(harness):
    h = harness
    with pytest.raises(ValueError):
        h.service.execute(h.user, 0, 'configure_user', user_id=h.initial.users[0].id, enabled=False)
    assert h.current[0] == h.initial
