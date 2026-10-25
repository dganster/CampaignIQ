"""Development-only account directory and authorization policy.

Pure, immutable policy objects. No database, environment, Streamlit, migrations,
or production activation. Callers must supply a server-authenticated Identity;
never construct one from unverified form fields or account-selector claims.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import re
from urllib.parse import urlsplit
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5


def _text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError(f"Invalid {field}")
    if value != value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError(f"Invalid {field}")


def _uuid(value: str) -> None:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Expected a canonical UUID")


def _workspace(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"workspace-[A-Za-z0-9][A-Za-z0-9_-]{0,95}", value):
        raise ValueError("Invalid workspace binding")


@dataclass(frozen=True, slots=True)
class Identity:
    issuer: str
    subject: str

    def __post_init__(self):
        _text(self.issuer, "issuer")
        _text(self.subject, "subject")
        url = urlsplit(self.issuer)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Expected a trusted HTTPS OIDC issuer")


class Permission(StrEnum):
    VIEW = "view"
    EDIT = "edit"


@dataclass(frozen=True, slots=True)
class User:
    id: str
    identity: Identity
    name: str
    workspace_id: str
    admin: bool = False
    enabled: bool = True

    def __post_init__(self):
        _uuid(self.id)
        if not isinstance(self.identity, Identity):
            raise ValueError("Invalid identity")
        _text(self.name, "user name")
        _workspace(self.workspace_id)
        if type(self.admin) is not bool or type(self.enabled) is not bool:
            raise ValueError("User flags must be boolean")


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    owner_id: str
    workspace_id: str
    name: str
    broker: str
    archived: bool = False
    legacy: bool = False

    def __post_init__(self):
        _uuid(self.id)
        _uuid(self.owner_id)
        _workspace(self.workspace_id)
        _text(self.name, "account name")
        _text(self.broker, "broker")
        if type(self.archived) is not bool or type(self.legacy) is not bool:
            raise ValueError("Account flags must be boolean")

    @property
    def storage_scope(self) -> str:
        """Proposed binding for future storage wiring, not a runtime switch."""
        return self.workspace_id if self.legacy else "account-" + UUID(self.id).hex


@dataclass(frozen=True, slots=True)
class Grant:
    user_id: str
    account_id: str
    permission: Permission

    def __post_init__(self):
        _uuid(self.user_id)
        _uuid(self.account_id)
        if not isinstance(self.permission, Permission):
            raise ValueError("Invalid permission")


@dataclass(frozen=True, slots=True)
class AuditEvent:
    sequence: int
    actor_id: str
    action: str
    target_id: str
    details: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        _uuid(self.actor_id)
        _uuid(self.target_id)
        _text(self.action, "audit action")
        if type(self.details) is not tuple:
            raise ValueError("Audit details must be immutable")
        keys = set()
        for pair in self.details:
            if type(pair) is not tuple or len(pair) != 2:
                raise ValueError("Invalid audit detail")
            key, value = pair
            _text(key, "audit key")
            _text(value, "audit value")
            if key in keys:
                raise ValueError("Duplicate audit detail")
            keys.add(key)


@dataclass(frozen=True, slots=True)
class AuthorizedAccount:
    account_id: str
    owner_workspace: str
    storage_scope: str
    writable: bool


@dataclass(frozen=True, slots=True)
class Directory:
    users: tuple[User, ...]
    accounts: tuple[Account, ...] = ()
    grants: tuple[Grant, ...] = ()
    events: tuple[AuditEvent, ...] = ()

    def __post_init__(self):
        for name, kind in (("users", User), ("accounts", Account), ("grants", Grant), ("events", AuditEvent)):
            value = getattr(self, name)
            if type(value) is not tuple or any(not isinstance(x, kind) for x in value):
                raise ValueError("Directory collections must be typed immutable tuples")
        users = {u.id: u for u in self.users}
        accounts = {a.id: a for a in self.accounts}
        if (len(users) != len(self.users) or len({u.identity for u in self.users}) != len(users)
                or len({u.workspace_id for u in self.users}) != len(users)):
            raise ValueError("Duplicate user, identity or workspace")
        if not any(u.enabled and u.admin for u in self.users):
            raise ValueError("At least one enabled administrator must remain")
        if len(accounts) != len(self.accounts) or len({a.storage_scope for a in self.accounts}) != len(accounts):
            raise ValueError("Duplicate account or storage binding")
        for a in self.accounts:
            if a.owner_id not in users or a.workspace_id != users[a.owner_id].workspace_id:
                raise ValueError("Account owner/workspace binding differs")
        pairs = set()
        for g in self.grants:
            if g.user_id not in users or g.account_id not in accounts:
                raise ValueError("Dangling grant")
            if accounts[g.account_id].owner_id == g.user_id:
                raise ValueError("Owner permission is implicit")
            pair = (g.user_id, g.account_id)
            if pair in pairs:
                raise ValueError("Duplicate grant")
            pairs.add(pair)
        for i, event in enumerate(self.events, 1):
            if type(event.sequence) is not int or event.sequence != i or event.actor_id not in users:
                raise ValueError("Invalid audit sequence or actor")

    @property
    def revision(self) -> int:
        return len(self.events)

    def user(self, identity: Identity) -> User:
        for u in self.users:
            if u.identity == identity and u.enabled:
                return u
        raise PermissionError("Access denied")

    def _admin(self, identity: Identity, expected_revision: int) -> User:
        actor = self.user(identity)
        if not actor.admin:
            raise PermissionError("Administrator permission required")
        if type(expected_revision) is not int or expected_revision != self.revision:
            raise ValueError("Directory changed; reload before applying this command")
        return actor

    def _changed(self, actor: User, action: str, target: str, *, details=(), **values) -> Directory:
        event = AuditEvent(self.revision + 1, actor.id, action, target, details)
        return replace(self, events=self.events + (event,), **values)

    def configure_user(self, actor: Identity, user_id: str, *, name: str | None = None,
                       enabled: bool | None = None, admin: bool | None = None,
                       expected_revision: int) -> Directory:
        acting = self._admin(actor, expected_revision)
        target = next((u for u in self.users if u.id == user_id), None)
        if target is None:
            raise ValueError("Unknown user")
        updated = replace(target, **{k: v for k, v in dict(name=name, enabled=enabled, admin=admin).items() if v is not None})
        return self._changed(acting, "configure_user", user_id,
                             details=(("name", updated.name), ("enabled", str(updated.enabled)), ("admin", str(updated.admin))),
                             users=tuple(updated if u.id == user_id else u for u in self.users))

    def add_user(self, actor: Identity, identity: Identity, name: str, *, expected_revision: int) -> Directory:
        acting = self._admin(actor, expected_revision)
        identifier = str(uuid4())
        user = User(identifier, identity, name, "workspace-" + UUID(identifier).hex)
        return self._changed(acting, "add_user", identifier, details=(("name", user.name),), users=self.users + (user,))

    def add_account(self, actor: Identity, owner_id: str, name: str, broker: str, *, expected_revision: int) -> Directory:
        acting = self._admin(actor, expected_revision)
        owner = next((u for u in self.users if u.id == owner_id and u.enabled), None)
        if owner is None:
            raise ValueError("Enabled account owner required")
        account = Account(str(uuid4()), owner.id, owner.workspace_id, name, broker)
        return self._changed(acting, "add_account", account.id,
                             details=(("owner_id", owner.id), ("name", name), ("broker", broker)), accounts=self.accounts + (account,))

    def configure_account(self, actor: Identity, account_id: str, *, name: str | None = None,
                          archived: bool | None = None, expected_revision: int) -> Directory:
        acting = self._admin(actor, expected_revision)
        target = next((a for a in self.accounts if a.id == account_id), None)
        if target is None:
            raise ValueError("Unknown account")
        updated = replace(target, **{k: v for k, v in dict(name=name, archived=archived).items() if v is not None})
        return self._changed(acting, "configure_account", account_id,
                             details=(("name", updated.name), ("archived", str(updated.archived))),
                             accounts=tuple(updated if a.id == account_id else a for a in self.accounts))

    def set_grant(self, actor: Identity, user_id: str, account_id: str,
                  permission: Permission | None, *, expected_revision: int) -> Directory:
        acting = self._admin(actor, expected_revision)
        if not any(u.id == user_id for u in self.users) or not any(a.id == account_id for a in self.accounts):
            raise ValueError("Unknown user or account")
        remaining = tuple(g for g in self.grants if (g.user_id, g.account_id) != (user_id, account_id))
        if permission is not None:
            remaining += (Grant(user_id, account_id, permission),)
        return self._changed(acting, "grant_access" if permission else "revoke_access", account_id,
                             details=(("user_id", user_id), ("permission", permission.value if permission else "none")), grants=remaining)

    def authorize(self, identity: Identity, account_id: str, *, write: bool = False) -> AuthorizedAccount:
        if type(write) is not bool:
            raise ValueError("Write flag must be boolean")
        user = self.user(identity)
        account = next((a for a in self.accounts if a.id == account_id), None)
        grant = next((g.permission for g in self.grants if g.user_id == user.id and g.account_id == account_id), None)
        if account is None or (account.owner_id != user.id and grant is None):
            raise PermissionError("Access denied")
        writable = not account.archived and (account.owner_id == user.id or grant is Permission.EDIT)
        if write and not writable:
            raise PermissionError("Account is read-only")
        return AuthorizedAccount(account.id, account.workspace_id, account.storage_scope, writable)

    def select(self, identity: Identity, account_ids: tuple[str, ...]) -> tuple[AuthorizedAccount, ...]:
        """Authorize the ENTIRE selection; never silently omit forbidden accounts."""
        self.user(identity)
        if type(account_ids) is not tuple or not account_ids or len(set(account_ids)) != len(account_ids):
            raise ValueError("Select distinct accounts explicitly")
        return tuple(self.authorize(identity, account_id) for account_id in account_ids)


@dataclass(frozen=True, slots=True)
class LegacyOwner:
    identity: Identity
    workspace_id: str
    name: str
    account_name: str
    broker: str


def propose_legacy_directory(owners: tuple[LegacyOwner, ...], administrator: Identity) -> Directory:
    """Explicit offline proposal from trusted owners; no data reads or writes.

    Ambiguous multiple-owner workspaces are rejected for review rather than
    silently transferring ownership. Never infer brokerage ownership from names.
    """
    if type(owners) is not tuple or any(not isinstance(o, LegacyOwner) for o in owners):
        raise ValueError("Explicit immutable legacy-owner records required")
    users, accounts = [], []
    for owner in owners:
        user_id = str(uuid5(NAMESPACE_URL, "campaigniq/user/" + repr((owner.identity.issuer, owner.identity.subject))))
        user = User(user_id, owner.identity, owner.name, owner.workspace_id, admin=owner.identity == administrator)
        account = Account(str(uuid5(NAMESPACE_URL, "campaigniq/legacy/" + owner.workspace_id)),
                          user.id, user.workspace_id, owner.account_name, owner.broker, legacy=True)
        users.append(user)
        accounts.append(account)
    return Directory(tuple(users), tuple(accounts))
