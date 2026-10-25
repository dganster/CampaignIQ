"""Behavioral policy tests; no database or application runtime required."""
from dataclasses import FrozenInstanceError, replace
from uuid import uuid4

import pytest

from campaigniq.account_administration import (
    Account, Directory, Grant, Identity, LegacyOwner, Permission,
    propose_legacy_directory,
)


ADMIN = Identity("https://accounts.google.com", "dennis-authenticated-subject")
ANDREW = Identity("https://accounts.google.com", "andrew-authenticated-subject")


@pytest.fixture
def directory():
    return propose_legacy_directory((
        LegacyOwner(ADMIN, "workspace-dennis", "Dennis", "Existing account", "Schwab"),
        LegacyOwner(ANDREW, "workspace-andrew", "Andrew", "Existing account", "Schwab"),
    ), ADMIN)


def test_legacy_proposal_preserves_exact_storage_bindings(directory):
    assert directory.authorize(ADMIN, directory.accounts[0].id).storage_scope == "workspace-dennis"
    assert directory.authorize(ANDREW, directory.accounts[1].id).storage_scope == "workspace-andrew"
    assert directory.revision == 0


def test_legacy_proposal_is_repeatable_without_random_ownership(directory):
    again = propose_legacy_directory((
        LegacyOwner(ADMIN, "workspace-dennis", "Dennis", "Existing account", "Schwab"),
        LegacyOwner(ANDREW, "workspace-andrew", "Andrew", "Existing account", "Schwab"),
    ), ADMIN)
    assert again == directory


@pytest.mark.parametrize("actor,index", [(ADMIN, 1), (ANDREW, 0)])
def test_admin_role_and_matching_account_labels_do_not_grant_financial_access(directory, actor, index):
    with pytest.raises(PermissionError):
        directory.authorize(actor, directory.accounts[index].id)


def test_same_subject_from_different_issuer_does_not_impersonate_admin(directory):
    other = Identity("https://untrusted.example", ADMIN.subject)
    with pytest.raises(PermissionError):
        directory.add_account(other, directory.users[0].id, "Other", "Other", expected_revision=0)


def test_unknown_account_and_unknown_identity_fail_closed(directory):
    for identity, account in [(ADMIN, str(uuid4())), (Identity(ADMIN.issuer, "unknown"), directory.accounts[0].id)]:
        with pytest.raises(PermissionError):
            directory.authorize(identity, account)


def test_multiple_broker_accounts_have_distinct_scopes_even_with_same_label(directory):
    d = directory.add_account(ADMIN, directory.users[0].id, "IRA", "Schwab", expected_revision=0)
    d = d.add_account(ADMIN, directory.users[0].id, "IRA", "Other broker", expected_revision=1)
    scopes = [d.authorize(ADMIN, a.id).storage_scope for a in (d.accounts[0], *d.accounts[2:])]
    assert len(set(scopes)) == 3
    assert scopes[0] == "workspace-dennis"
    assert all(len(scope) <= 128 for scope in scopes)


def test_new_user_is_not_admin_and_cannot_see_other_accounts(directory):
    identity = Identity(ADMIN.issuer, "new-user")
    d = directory.add_user(ADMIN, identity, "New member", expected_revision=0)
    assert d.user(identity).admin is False
    with pytest.raises(PermissionError):
        d.authorize(identity, d.accounts[0].id)


@pytest.mark.parametrize("command", ["user", "account", "grant", "new_user", "new_account"])
def test_member_cannot_perform_administration(directory, command):
    with pytest.raises(PermissionError):
        if command == "user":
            directory.configure_user(ANDREW, directory.users[0].id, enabled=False, expected_revision=0)
        elif command == "account":
            directory.configure_account(ANDREW, directory.accounts[1].id, archived=True, expected_revision=0)
        elif command == "grant":
            directory.set_grant(ANDREW, directory.users[1].id, directory.accounts[0].id, Permission.EDIT, expected_revision=0)
        elif command == "new_user":
            directory.add_user(ANDREW, Identity(ADMIN.issuer, "new"), "New", expected_revision=0)
        else:
            directory.add_account(ANDREW, directory.users[1].id, "IRA", "Broker", expected_revision=0)


def test_view_edit_and_revoke_are_enforced_and_auditable(directory):
    user, account = directory.users[1].id, directory.accounts[0].id
    d = directory.set_grant(ADMIN, user, account, Permission.VIEW, expected_revision=0)
    assert not d.authorize(ANDREW, account).writable
    with pytest.raises(PermissionError):
        d.authorize(ANDREW, account, write=True)
    d = d.set_grant(ADMIN, user, account, Permission.EDIT, expected_revision=1)
    assert d.authorize(ANDREW, account, write=True).writable
    d = d.set_grant(ADMIN, user, account, None, expected_revision=2)
    with pytest.raises(PermissionError):
        d.authorize(ANDREW, account)
    assert dict(d.events[1].details) == {"user_id": user, "permission": "edit"}
    assert [e.sequence for e in d.events] == [1, 2, 3]
    assert directory.grants == () and directory.events == ()


def test_disabled_user_loses_access_without_deleting_records_or_grants(directory):
    user, account = directory.users[1].id, directory.accounts[0].id
    d = directory.set_grant(ADMIN, user, account, Permission.EDIT, expected_revision=0)
    d = d.configure_user(ADMIN, user, enabled=False, expected_revision=1)
    assert len(d.users) == 2 and len(d.grants) == 1 and d.accounts == directory.accounts
    for target in (directory.accounts[0].id, directory.accounts[1].id):
        with pytest.raises(PermissionError):
            d.authorize(ANDREW, target)
    d = d.configure_user(ADMIN, user, enabled=True, expected_revision=2)
    assert d.authorize(ANDREW, account, write=True).writable


@pytest.mark.parametrize("change", [{"enabled": False}, {"admin": False}])
def test_last_enabled_administrator_cannot_be_removed(directory, change):
    with pytest.raises(ValueError, match="administrator"):
        directory.configure_user(ADMIN, directory.users[0].id, expected_revision=0, **change)
    assert directory.user(ADMIN).admin


def test_admin_handover_and_disabled_admin_cannot_issue_commands(directory):
    d = directory.configure_user(ADMIN, directory.users[1].id, admin=True, expected_revision=0)
    d = d.configure_user(ANDREW, directory.users[0].id, enabled=False, expected_revision=1)
    with pytest.raises(PermissionError):
        d.add_account(ADMIN, directory.users[1].id, "IRA", "Schwab", expected_revision=2)


def test_archived_account_remains_readable_but_cannot_be_written(directory):
    account = directory.accounts[0]
    d = directory.configure_account(ADMIN, account.id, archived=True, expected_revision=0)
    assert d.authorize(ADMIN, account.id).storage_scope == account.storage_scope
    with pytest.raises(PermissionError):
        d.authorize(ADMIN, account.id, write=True)
    d = d.configure_account(ADMIN, account.id, archived=False, expected_revision=1)
    assert d.authorize(ADMIN, account.id, write=True).writable


def test_account_rename_preserves_campaign_storage_and_identity(directory):
    a = directory.accounts[0]
    d = directory.configure_account(ADMIN, a.id, name="My taxable account", expected_revision=0)
    assert d.accounts[0].id == a.id and d.accounts[0].storage_scope == a.storage_scope


def test_consolidated_selection_rejects_entire_selection_if_any_account_forbidden(directory):
    with pytest.raises(PermissionError):
        directory.select(ADMIN, tuple(a.id for a in directory.accounts))
    d = directory.set_grant(ADMIN, directory.users[0].id, directory.accounts[1].id, Permission.VIEW, expected_revision=0)
    selected = d.select(ADMIN, tuple(a.id for a in d.accounts))
    assert tuple(s.storage_scope for s in selected) == ("workspace-dennis", "workspace-andrew")
    assert len({s.account_id for s in selected}) == 2


@pytest.mark.parametrize("selection", [(), ("same", "same"), ["one"]])
def test_empty_duplicate_or_mutable_report_selection_rejected(directory, selection):
    with pytest.raises(ValueError):
        directory.select(ADMIN, selection)


@pytest.mark.parametrize("revision", [-1, 1, True, "0"])
def test_stale_or_invalid_revision_cannot_apply_admin_command(directory, revision):
    with pytest.raises(ValueError, match="reload"):
        directory.configure_account(ADMIN, directory.accounts[0].id, archived=True, expected_revision=revision)


def test_stale_snapshot_cannot_apply_followup_command(directory):
    d = directory.configure_account(ADMIN, directory.accounts[0].id, name="Updated", expected_revision=0)
    with pytest.raises(ValueError, match="reload"):
        d.configure_user(ADMIN, d.users[1].id, enabled=False, expected_revision=0)


@pytest.mark.parametrize("invalid", ["../dennis", "workspace-../x", "/workspace-dennis", "shared", "default", "account-injected"])
def test_unsafe_or_reserved_workspace_bindings_rejected(directory, invalid):
    with pytest.raises(ValueError):
        replace(directory.users[0], workspace_id=invalid)


def test_ambiguous_legacy_owners_rejected_instead_of_guessing(directory):
    with pytest.raises(ValueError, match="workspace"):
        propose_legacy_directory((
            LegacyOwner(ADMIN, "workspace-dennis", "Dennis", "Account", "Schwab"),
            LegacyOwner(ANDREW, "workspace-dennis", "Andrew", "Account", "Schwab"),
        ), ADMIN)


def test_bootstrap_requires_existing_explicit_admin_and_keeps_immutable_inputs(directory):
    with pytest.raises(ValueError):
        propose_legacy_directory((), ADMIN)
    with pytest.raises(FrozenInstanceError):
        directory.users[0].admin = False
    with pytest.raises(ValueError):
        Directory(list(directory.users))


def test_duplicate_identity_or_dangling_grant_rejected(directory):
    with pytest.raises(ValueError):
        directory.add_user(ADMIN, ANDREW, "Impersonation", expected_revision=0)
    with pytest.raises(ValueError):
        replace(directory, grants=(Grant(str(uuid4()), directory.accounts[0].id, Permission.VIEW),))


@pytest.mark.parametrize("bad", [1, "false"])
def test_flags_are_not_coerced_from_form_input(directory, bad):
    with pytest.raises(ValueError):
        directory.configure_user(ADMIN, directory.users[1].id, enabled=bad, expected_revision=0)
    with pytest.raises(ValueError):
        directory.configure_account(ADMIN, directory.accounts[0].id, archived=bad, expected_revision=0)


def test_permission_strings_and_owner_override_grants_rejected(directory):
    for user, permission in [(directory.users[1].id, "edit"), (directory.users[0].id, Permission.VIEW)]:
        with pytest.raises(ValueError):
            directory.set_grant(ADMIN, user, directory.accounts[0].id, permission, expected_revision=0)
