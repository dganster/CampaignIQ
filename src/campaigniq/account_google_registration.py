"""Invitation enrollment from server-owned Google OIDC claims only.

Enrollment creates a disabled member with no accounts or grants. An enabled
administrator must subsequently approve the user through the audited directory.
"""
from dataclasses import replace
import re
from uuid import uuid4, UUID
from campaigniq.account_administration import Identity, User, AuditEvent
from campaigniq.ui.streamlit_identity import oidc_claims_from_streamlit_user


def gmail_address(value):
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError('Enter a Gmail address')
    value = value.strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9.]{0,63}@gmail\.com', value):
        raise ValueError('Enter a Gmail address')
    return value


def open_invitations(directory):
    consumed = {dict(e.details).get('invitation_id') for e in directory.events
                if e.action == 'google_registration_pending'}
    return tuple(e for e in directory.events if e.action == 'invite_google_user'
                 and e.target_id not in consumed)


def request_registration(directory, server_user, *, trusted_issuer):
    claims = oidc_claims_from_streamlit_user(server_user)
    if trusted_issuer != 'https://accounts.google.com' or claims.get('iss') != trusted_issuer:
        raise PermissionError('Verified Google sign-in required')
    try:
        identity = Identity(trusted_issuer, claims.get('sub'))
    except (ValueError, TypeError):
        raise PermissionError('Verified Google sign-in required') from None
    # Never re-enable an existing disabled user or attach another identity by email.
    existing = next((u for u in directory.users if u.identity == identity), None)
    if existing is not None:
        return directory
    if claims.get('email_verified') is not True:
        raise PermissionError('Verified Gmail address required')
    try:
        email = gmail_address(claims.get('email'))
    except ValueError:
        raise PermissionError('Verified Gmail address required') from None
    matches = [e for e in open_invitations(directory) if dict(e.details).get('email') == email]
    if len(matches) != 1:
        raise PermissionError('An administrator invitation is required')
    invitation = matches[0]
    inviter = next((u for u in directory.users if u.id == invitation.actor_id), None)
    if inviter is None or not inviter.enabled or not inviter.admin:
        raise PermissionError('An active administrator invitation is required')
    identifier = str(uuid4())
    pending = User(identifier, identity, dict(invitation.details)['name'],
                   'workspace-' + UUID(identifier).hex, enabled=False)
    event = AuditEvent(directory.revision + 1, identifier, 'google_registration_pending', identifier,
                       (('invitation_id', invitation.target_id), ('email', email)))
    return replace(directory, users=directory.users + (pending,), events=directory.events + (event,))
