from dataclasses import replace
from uuid import uuid4
import pytest
from campaigniq.account_administration import Identity, LegacyOwner, Permission, propose_legacy_directory
from campaigniq.account_signin import signed_in_accounts, verify_legacy_workspace_bindings

ISSUER = "https://accounts.example.test"
A, B = Identity(ISSUER, "subject-a"), Identity(ISSUER, "subject-b")

class Session:
    def __init__(self, **claims): self.claims = claims
    def to_dict(self): return dict(self.claims)

def directory():
    return propose_legacy_directory((LegacyOwner(A, "workspace-a", "A", "Broker A", "schwab"),
                                    LegacyOwner(B, "workspace-b", "B", "Broker B", "schwab")), A)

def session(**changes):
    return Session(**(dict(is_logged_in=True, iss=ISSUER, sub=A.subject) | changes))

def test_owner_and_admin_do_not_gain_other_account_data():
    d = directory()
    result = signed_in_accounts(session(), d, trusted_issuer=ISSUER)
    assert result.administrator
    assert result.account_ids == (d.accounts[0].id,)
    assert result.identity == A and result.user_id == d.users[0].id

@pytest.mark.parametrize("changes", [dict(is_logged_in=False), dict(is_logged_in=1),
    dict(iss="https://other.test"), dict(iss=None), dict(sub=None), dict(sub=""),
    dict(sub=" subject-a"), dict(sub="subject-a "), dict(sub="unknown"), dict(sub=3)])
def test_invalid_sessions_denied(changes):
    with pytest.raises(PermissionError): signed_in_accounts(session(**changes), directory(), trusted_issuer=ISSUER)

@pytest.mark.parametrize("user", [{"iss": ISSUER, "sub": A.subject, "is_logged_in": True}, None, object()])
def test_raw_browser_claims_not_a_server_session(user):
    with pytest.raises(PermissionError): signed_in_accounts(user, directory(), trusted_issuer=ISSUER)

def test_missing_claims_email_and_workspace_cannot_choose_identity():
    with pytest.raises(PermissionError):
        signed_in_accounts(Session(is_logged_in=True, email="admin@example.test", workspace_id="workspace-a"), directory(), trusted_issuer=ISSUER)

def test_grants_revocation_archive_and_disabled_rechecked():
    d = directory()
    target = d.accounts[1].id
    granted = d.set_grant(A, d.users[0].id, target, Permission.VIEW, expected_revision=0)
    assert len(signed_in_accounts(session(), granted, trusted_issuer=ISSUER).account_ids) == 2
    archived = granted.configure_account(A, target, archived=True, expected_revision=1)
    assert target in signed_in_accounts(session(), archived, trusted_issuer=ISSUER).account_ids
    assert not archived.authorize(A, target).writable
    revoked = archived.set_grant(A, d.users[0].id, target, None, expected_revision=2)
    assert target not in signed_in_accounts(session(), revoked, trusted_issuer=ISSUER).account_ids
    disabled = revoked.configure_user(A, d.users[1].id, enabled=False, expected_revision=3)
    with pytest.raises(PermissionError): signed_in_accounts(session(sub=B.subject), disabled, trusted_issuer=ISSUER)

def test_same_subject_other_issuer_not_same_person():
    with pytest.raises(PermissionError): signed_in_accounts(session(iss="https://other.test"), directory(), trusted_issuer="https://other.test")

def test_complete_legacy_mapping_preserves_scopes_without_mutation():
    d = directory()
    before = repr(d)
    bindings = verify_legacy_workspace_bindings(d, {A.subject:"workspace-a", B.subject:"workspace-b"}, trusted_issuer=ISSUER)
    assert set(bindings) == {(a.id, a.workspace_id) for a in d.accounts}
    assert repr(d) == before

@pytest.mark.parametrize("mapping", [{}, {A.subject:"workspace-a"}, {A.subject:"workspace-b", B.subject:"workspace-a"},
    {A.subject:"workspace-a", B.subject:"workspace-a"}, {A.subject:"../bad"}, {" subject-a":"workspace-a"}])
def test_incomplete_ambiguous_or_changed_legacy_mapping_rejected(mapping):
    with pytest.raises((ValueError, PermissionError)):
        verify_legacy_workspace_bindings(directory(), mapping, trusted_issuer=ISSUER)

def test_rebound_legacy_account_rejected():
    d = directory()
    changed = replace(d, accounts=(replace(d.accounts[0], legacy=False), d.accounts[1]))
    with pytest.raises(ValueError): verify_legacy_workspace_bindings(changed, {A.subject:"workspace-a", B.subject:"workspace-b"}, trusted_issuer=ISSUER)

@pytest.mark.parametrize("issuer", ["http://example.test", "", "https://user:password@example.test"])
def test_bad_server_configuration_rejected(issuer):
    with pytest.raises(ValueError): signed_in_accounts(session(), directory(), trusted_issuer=issuer)
