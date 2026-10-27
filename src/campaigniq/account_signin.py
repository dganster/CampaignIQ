"""Account authorization adapter for server-authenticated Streamlit OIDC users.

This module does not authenticate tokens. Only pass Streamlit's server-owned
st.user, never uploaded claims, request JSON, or a browser-selected identity.
No runtime activation, database access, or storage migration occurs here.
"""
from collections.abc import Mapping
from dataclasses import dataclass

from campaigniq.account_administration import Directory, Identity
from campaigniq.access import resolve_access_context
from campaigniq.ui.streamlit_identity import oidc_claims_from_streamlit_user


@dataclass(frozen=True)
class AccountSignIn:
    identity: Identity
    user_id: str
    directory_revision: int
    account_ids: tuple[str, ...]
    administrator: bool


def signed_in_accounts(user, directory: Directory, *, trusted_issuer: str) -> AccountSignIn:
    """Resolve a verified server session against a freshly loaded directory.

    trusted_issuer comes from server OIDC configuration, not user input.
    Issuer aliases and subject whitespace are deliberately not normalized.
    Re-run authorization inside each data transaction; this result is UI state,
    not a reusable capability and does not override later revocation.
    """
    if type(directory) is not Directory:
        raise ValueError("A current account directory is required")
    # Validate configuration even when the session is absent.
    Identity(trusted_issuer, "configuration-validation")
    claims = oidc_claims_from_streamlit_user(user)
    issuer, subject = claims.get("iss"), claims.get("sub")
    if issuer != trusted_issuer or not isinstance(subject, str):
        raise PermissionError("Sign-in is not authorized")
    try:
        identity = Identity(issuer, subject)
    except ValueError:
        raise PermissionError("Sign-in is not authorized") from None
    actor = directory.user(identity)
    accounts = []
    for account in directory.accounts:
        try:
            directory.authorize(identity, account.id)
        except PermissionError:
            continue
        accounts.append(account.id)
    return AccountSignIn(identity, actor.id, directory.revision,
                         tuple(accounts), actor.admin)


def verify_legacy_workspace_bindings(directory: Directory, authorization: Mapping[str, str],
                                     *, trusted_issuer: str) -> tuple[tuple[str, str], ...]:
    """Check the COMPLETE existing server subject-to-workspace mapping.

    Returns account ID / unchanged storage namespace pairs, never a migration.
    Rejects shared ownership, absent/extra legacy accounts, issuer ambiguity,
    normalized subjects, and stale ownership. New accounts and explicit grants
    do not change the legacy mapping. No names or email addresses infer owners.
    """
    Identity(trusted_issuer, "configuration-validation")
    if type(directory) is not Directory or not isinstance(authorization, Mapping) or not authorization:
        raise ValueError("Explicit current workspace authorization is required")
    expected = set()
    workspaces = set()
    for subject, workspace in authorization.items():
        identity = Identity(trusted_issuer, subject)
        if not isinstance(workspace, str) or workspace != workspace.strip():
            raise ValueError("Invalid workspace authorization")
        access = resolve_access_context(subject, authorization)
        if access is None or workspace in workspaces:
            raise ValueError("Ambiguous legacy workspace ownership")
        workspaces.add(workspace)
        owner = directory.user(identity)
        if owner.workspace_id != workspace:
            raise ValueError("Legacy owner workspace differs")
        matches = tuple(a for a in directory.accounts if a.legacy and a.owner_id == owner.id)
        if len(matches) != 1 or matches[0].storage_scope != workspace:
            raise ValueError("Legacy storage binding differs")
        expected.add((matches[0].id, workspace))
    actual = {(a.id, a.storage_scope) for a in directory.accounts if a.legacy}
    if actual != expected:
        raise ValueError("Legacy mapping is incomplete")
    return tuple(sorted(expected))
