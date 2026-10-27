"""Audited LOCAL directory commands attributed to the server's OIDC session.

No synthetic actor switch and no financial storage access. The existing local
directory transaction remains responsible for locked authorization and CAS.
"""
from types import MappingProxyType

from campaigniq.account_administration import Identity
from campaigniq.account_signin import signed_in_accounts, verify_legacy_workspace_bindings
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory


class LocalSignedInAdministration:
    def __init__(self, store, *, trusted_issuer, authorization):
        if type(store) is not LocalPostgresAccountDirectory:
            raise ValueError('Guarded local directory service required')
        Identity(trusted_issuer, 'configuration-validation')
        self._store = store
        self._issuer = trusted_issuer
        self._authorization = MappingProxyType(dict(authorization))

    @property
    def registry_id(self):
        return self._store.registry_id

    def _admin(self, user):
        directory = self._store._snapshot()
        verify_legacy_workspace_bindings(directory, self._authorization, trusted_issuer=self._issuer)
        actor = signed_in_accounts(user, directory, trusted_issuer=self._issuer)
        if not actor.administrator:
            raise PermissionError('Administrator permission required')
        return directory, actor

    def snapshot(self, user):
        """Full directory is restricted to a currently enabled administrator."""
        return self._admin(user)[0]

    def execute(self, user, expected_revision, command, /, **arguments):
        _, actor = self._admin(user)
        if command == 'add_user':
            identity = arguments.get('identity')
            if type(identity) is not Identity or identity.issuer != self._issuer:
                raise ValueError('New user must use the configured OIDC issuer')
        # Caller cannot pass an actor identity: store rechecks enabled/admin
        # under its update lock and rejects stale revisions in that transaction.
        return self._store.execute(actor.identity, expected_revision, command, **arguments)

    def add_user(self, user, expected_revision, *, name, subject):
        # Exact subject from an independently verified provider identity. No
        # email lookup, implicit enrollment, or workspace ownership inference.
        return self.execute(user, expected_revision, 'add_user',
                            name=name, identity=Identity(self._issuer, subject))
